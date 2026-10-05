"""Self-contained, validated discovery/query/retrieval journey for approved test patients."""

import atexit
import base64
import os
import random
import re
import tempfile
import uuid
from email.parser import BytesParser
from time import perf_counter
from xml.sax.saxutils import escape, quoteattr

from defusedxml import ElementTree as ET
from locust import HttpUser, between, events, task
from locust.exception import StopTest

NS = {
    "s": "http://www.w3.org/2003/05/soap-envelope",
    "hl7": "urn:hl7-org:v3",
    "query": "urn:oasis:names:tc:ebxml-regrep:xsd:query:3.0",
    "rim": "urn:oasis:names:tc:ebxml-regrep:xsd:rim:3.0",
    "rs": "urn:oasis:names:tc:ebxml-regrep:xsd:rs:3.0",
    "xds": "urn:ihe:iti:xds-b:2007",
}
NHS_ROOT = "2.16.840.1.113883.2.1.4.1"
SUCCESS = "urn:oasis:names:tc:ebxml-regrep:ResponseStatusType:Success"
UNIQUE_ID_SCHEME = "urn:uuid:2e82c1f6-a085-4c72-9da3-8640a32e42ab"
CERT_FILE = None


def load_certificate():
    """Resolve once per worker; fail setup if a configured certificate cannot load."""
    global CERT_FILE
    if CERT_FILE:
        return CERT_FILE
    if path := os.getenv("LOCUST_CERT_FILE"):
        CERT_FILE = path
    elif vault := os.getenv("KEY_VAULT_URL"):
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient

        with DefaultAzureCredential() as credential:
            with SecretClient(vault_url=vault, credential=credential) as client:
                pem = client.get_secret(os.getenv("PEM_SECRET_NAME", "epic-ca-cert")).value
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pem") as cert:
            cert.write(pem.encode("utf-8"))
        CERT_FILE = cert.name
        atexit.register(os.remove, CERT_FILE)
    return CERT_FILE


@events.init.add_listener
def configure_worker(environment, **kwargs):
    try:
        load_certificate()
    except Exception:
        raise StopTest("Unable to load the configured load-test certificate") from None


class JourneyFailure(Exception):
    """Fixed messages only: never include identifiers, XML, or upstream error bodies."""


def require(condition, message):
    if not condition:
        raise JourneyFailure(message)


def envelope(nhs_number, action, body):
    attributes = {
        "urn:oasis:names:tc:xspa:1.0:subject:subject-id": os.getenv("LOCUST_SUBJECT_ID", "xhuma-load-test"),
        "urn:oasis:names:tc:xspa:1.0:subject:organization": os.getenv("LOCUST_ORGANIZATION", "Xhuma load test"),
        "urn:oasis:names:tc:xspa:1.0:subject:organization-id": os.getenv("LOCUST_ORGANIZATION_ID", "RRV00"),
        "urn:nhin:names:saml:homeCommunityId": os.getenv("LOCUST_HOME_COMMUNITY_ID", "2.16.840.1.113883.2.1.3.34.9001"),
        "urn:oasis:names:tc:xacml:2.0:resource:resource-id": f"{nhs_number}^^^&{NHS_ROOT}&ISO",
    }
    saml_attributes = "".join(
        f"<saml2:Attribute Name={quoteattr(name)}><saml2:AttributeValue>{escape(value)}</saml2:AttributeValue></saml2:Attribute>"
        for name, value in attributes.items()
    )
    saml_attributes += (
        '<saml2:Attribute Name="urn:oasis:names:tc:xacml:2.0:subject:role"><saml2:AttributeValue>'
        '<Role code="224608005" codeSystem="2.16.840.1.113883.6.96" displayName="Administrative healthcare staff"/>'
        "</saml2:AttributeValue></saml2:Attribute>"
        '<saml2:Attribute Name="urn:oasis:names:tc:xspa:1.0:subject:purposeofuse"><saml2:AttributeValue>'
        '<PurposeForUse code="TREATMENT" codeSystem="2.16.840.1.113883.3.18.7.1" displayName="Treatment"/>'
        "</saml2:AttributeValue></saml2:Attribute>"
    )
    # ITI-39's current multipart extractor expects the envelope on one line.
    return (
        f'<s:Envelope xmlns:s="{NS["s"]}" xmlns:a="http://www.w3.org/2005/08/addressing" '
        'xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd" '
        'xmlns:saml2="urn:oasis:names:tc:SAML:2.0:assertion">'
        f'<s:Header><a:MessageID>urn:uuid:{uuid.uuid4()}</a:MessageID><a:Action s:mustUnderstand="1">{action}</a:Action>'
        "<a:ReplyTo><a:Address>http://www.w3.org/2005/08/addressing/anonymous</a:Address></a:ReplyTo>"
        f'<wsse:Security s:mustUnderstand="1"><saml2:Assertion ID="_{uuid.uuid4()}" Version="2.0">'
        f"<saml2:Issuer>{escape(os.getenv('LOCUST_SAML_ISSUER', 'urn:nhs:names:services:spine'))}</saml2:Issuer>"
        f"<saml2:AttributeStatement>{saml_attributes}</saml2:AttributeStatement>"
        f"</saml2:Assertion></wsse:Security></s:Header><s:Body>{body}</s:Body></s:Envelope>"
    )


def discovery_payload(nhs_number):
    return envelope(
        nhs_number,
        "urn:hl7-org:v3:PRPA_IN201305UV02:CrossGatewayPatientDiscovery",
        '<PRPA_IN201305UV02 xmlns="urn:hl7-org:v3" ITSVersion="XML_1.0">'
        '<controlActProcess classCode="CACT" moodCode="EVN"><queryByParameter>'
        f'<queryId root="{uuid.uuid4()}"/><statusCode code="new"/>'
        '<responseModalityCode code="R"/><responsePriorityCode code="I"/><parameterList><livingSubjectId>'
        f'<value root="{NHS_ROOT}" extension={quoteattr(nhs_number)}/>'
        "</livingSubjectId></parameterList></queryByParameter></controlActProcess></PRPA_IN201305UV02>",
    )


def query_payload(nhs_number):
    patient_id = escape(f"'{nhs_number}^^^&{NHS_ROOT}&ISO'")
    return envelope(
        nhs_number,
        "urn:ihe:iti:2007:CrossGatewayQuery",
        f'<query:AdhocQueryRequest xmlns:query="{NS["query"]}" xmlns:rim="{NS["rim"]}">'
        '<query:ResponseOption returnComposedObjects="true" returnType="LeafClass"/>'
        '<rim:AdhocQuery id="urn:uuid:14d4debf-8f97-4251-9a74-a90016b0af0d">'
        f'<rim:Slot name="$XDSDocumentEntryPatientId"><rim:ValueList><rim:Value>{patient_id}</rim:Value></rim:ValueList></rim:Slot>'
        '<rim:Slot name="$XDSDocumentEntryStatus"><rim:ValueList><rim:Value>'
        "('urn:ihe:iti:2010:StatusType:DeferredCreation')"
        "</rim:Value></rim:ValueList></rim:Slot></rim:AdhocQuery></query:AdhocQueryRequest>",
    )


def retrieval_payload(nhs_number, community, repository, document):
    community = community.removeprefix("urn:oid:")
    payload = envelope(
        nhs_number,
        "urn:ihe:iti:2007:CrossGatewayRetrieve",
        f'<xds:RetrieveDocumentSetRequest xmlns:xds="{NS["xds"]}"><xds:DocumentRequest>'
        f"<xds:HomeCommunityId>urn:oid:{escape(community)}</xds:HomeCommunityId>"
        f"<xds:RepositoryUniqueId>{escape(repository)}</xds:RepositoryUniqueId>"
        f"<xds:DocumentUniqueId>{escape(document)}</xds:DocumentUniqueId>"
        "</xds:DocumentRequest></xds:RetrieveDocumentSetRequest>",
    )
    boundary = f"uuid:{uuid.uuid4()}"
    body = (
        f'--{boundary}\r\nContent-Type: application/xop+xml; charset=UTF-8; type="application/soap+xml"\r\n'
        f"Content-ID: <soap-root>\r\n\r\n{payload}\r\n--{boundary}--\r\n"
    )
    content_type = (
        f'multipart/related; type="application/xop+xml"; start="<soap-root>"; '
        f'start-info="application/soap+xml"; boundary="{boundary}"'
    )
    return body, content_type


def response_envelope(response):
    require(response.status_code == 200, f"HTTP {response.status_code}")
    content = response.content
    if "multipart/" in response.headers.get("Content-Type", "").lower():
        # The service currently includes MIME headers in its response body as well.
        if not content.lstrip().lower().startswith((b"content-type:", b"mime-version:")):
            content = (
                f"Content-Type: {response.headers['Content-Type']}\r\nMIME-Version: 1.0\r\n\r\n".encode() + content
            )
        message = BytesParser().parsebytes(content)
        candidates = [part.get_payload(decode=True) for part in message.walk() if not part.is_multipart()]
    else:
        candidates = [content]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            root = ET.fromstring(candidate)
        except Exception:
            continue
        if root.tag == f"{{{NS['s']}}}Envelope":
            require(root.find("s:Body/s:Fault", NS) is None, "SOAP Fault")
            return root
    raise JourneyFailure("Missing or invalid SOAP envelope")


def validate_discovery(root, nhs_number):
    code = root.find(".//hl7:queryResponseCode", NS)
    require(code is not None and code.get("code") == "OK", "ITI-55 query did not succeed")
    identifiers = root.findall(".//hl7:patient/hl7:id", NS)
    require(
        any(item.get("extension") == nhs_number and item.get("root") == NHS_ROOT for item in identifiers),
        "ITI-55 patient mismatch or absent",
    )
    community = root.find(".//hl7:custodian/hl7:assignedEntity/hl7:id", NS)
    require(community is not None and community.get("root"), "ITI-55 missing home community")
    return community.get("root")


def validate_query(root):
    result = root.find("s:Body/query:AdhocQueryResponse", NS)
    require(result is not None and result.get("status") == SUCCESS, "ITI-38 registry query failed")
    for entry in result.findall("rim:RegistryObjectList/rim:ExtrinsicObject", NS):
        repository = entry.find('rim:Slot[@name="repositoryUniqueId"]/rim:ValueList/rim:Value', NS)
        document = entry.find(f'rim:ExternalIdentifier[@identificationScheme="{UNIQUE_ID_SCHEME}"]', NS)
        if repository is not None and repository.text and document is not None and document.get("value"):
            return repository.text, document.get("value")
    raise JourneyFailure("ITI-38 missing retrievable document metadata")


def validate_retrieval(root, nhs_number, document_id):
    result = root.find("s:Body/xds:RetrieveDocumentSetResponse", NS)
    require(result is not None, "ITI-39 missing retrieval response")
    status = result.find("rs:RegistryResponse", NS)
    require(status is not None and status.get("status") == SUCCESS, "ITI-39 registry retrieval failed")
    document = result.find("xds:DocumentResponse", NS)
    require(document is not None, "ITI-39 missing document")
    require(
        document.findtext("xds:DocumentUniqueId", namespaces=NS) == document_id, "ITI-39 document identifier mismatch"
    )
    encoded = document.findtext("xds:Document", namespaces=NS)
    require(bool(encoded), "ITI-39 empty document")
    try:
        decoded = base64.b64decode("".join(encoded.split()), validate=True)
        clinical = ET.fromstring(decoded)
    except Exception:
        raise JourneyFailure("ITI-39 invalid base64 or C-CDA XML") from None
    require(clinical.tag == "{urn:hl7-org:v3}ClinicalDocument", "ITI-39 invalid C-CDA root")
    ids = clinical.findall("hl7:recordTarget/hl7:patientRole/hl7:id", NS)
    require(
        any(item.get("extension") == nhs_number and item.get("root") == NHS_ROOT for item in ids),
        "ITI-39 C-CDA patient mismatch",
    )
    return len(decoded)


class EpicClientUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        self.patients = [
            value.strip() for value in os.getenv("LOCUST_PATIENTS", "9692136744").split(",") if value.strip()
        ]
        if not self.patients or any(not re.fullmatch(r"\d{10}", value) for value in self.patients):
            raise ValueError(
                "LOCUST_PATIENTS must contain comma-separated, ten-digit approved test patient identifiers"
            )
        self.client.cert = load_certificate()

    def post_soap(self, endpoint, payload, validate, trace_id, content_type="application/soap+xml; charset=UTF-8"):
        failure = None
        value = None
        with self.client.post(
            f"/SOAP/{endpoint}",
            data=payload.encode("utf-8"),
            name=f"/SOAP/{endpoint}",
            headers={"Content-Type": content_type, "traceparent": f"00-{trace_id}-{uuid.uuid4().hex[:16]}-01"},
            catch_response=True,
            context={"trace_id": trace_id},
        ) as response:
            try:
                value = validate(response_envelope(response))
            except JourneyFailure as exc:
                failure = exc
                response.failure(str(exc))
            except Exception:
                failure = JourneyFailure("Unexpected response validation failure")
                response.failure(str(failure))
        if failure:
            raise failure
        return value

    @task
    def document_journey(self):
        nhs_number = random.choice(self.patients)
        trace_id = uuid.uuid4().hex
        started = perf_counter()
        failure = None
        document_bytes = 0
        try:
            community = self.post_soap(
                "iti55", discovery_payload(nhs_number), lambda root: validate_discovery(root, nhs_number), trace_id
            )
            repository, document = self.post_soap("iti38", query_payload(nhs_number), validate_query, trace_id)
            body, content_type = retrieval_payload(nhs_number, community, repository, document)
            document_bytes = self.post_soap(
                "iti39", body, lambda root: validate_retrieval(root, nhs_number, document), trace_id, content_type
            )
        except JourneyFailure as exc:
            failure = exc
        except Exception:
            failure = JourneyFailure("Unexpected journey failure")
        finally:
            self.environment.events.request.fire(
                request_type="FLOW",
                name="Discovery → query → retrieval",
                response_time=(perf_counter() - started) * 1000,
                response_length=document_bytes,
                exception=failure,
                context={"trace_id": trace_id},
            )
