"""Verify the incoming caller, then issue a fresh Xhuma assertion for NIR.

This is application code: it never imports the Q23 harness or fixture identities.
The enabled integration deliberately accepts a narrow inbound profile: one signed
SAML assertion, a finite validity window and a signed NHS resource-id. Transport
authentication remains the existing mTLS middleware's responsibility.
"""

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import xmltodict
from cryptography.hazmat.primitives import serialization
from lxml import etree as ET
from signxml import DigestAlgorithm, SignatureConfiguration, SignatureMethod, XMLSigner, XMLVerifier

from app.audit.audit import process_saml_attributes
from app.audit.models import SAMLAttributes
from app.soap.faults import SoapFault
from app.soap.nir_queries import NHS_NUMBER_ROOT, QUERY_NS, RIM_NS, SOAP_NS, WSA_NS, build_iti38_request
from app.soap.saml_helper import validate_saml_attributes

from .config import NIRDiscoveryConfig
from .saml_models import (
    AudienceRestriction,
    BearerConfirmation,
    BearerConfirmationData,
    SAMLAssertion,
    SAMLConditions,
    SAMLSubject,
    WSSecurity,
)

SAML = "urn:oasis:names:tc:SAML:2.0:assertion"
WSSE = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
DS = "http://www.w3.org/2000/09/xmldsig#"
HL7 = "urn:hl7-org:v3"
C14N = "http://www.w3.org/2001/10/xml-exc-c14n#"
HOME = "urn:ihe:iti:xca:2010:homeCommunityId"
LEGACY_HOME = "urn:nhin:names:saml:homeCommunityId"
SUBJECT = "urn:oasis:names:tc:xspa:1.0:subject:subject-id"
RESOURCE = "urn:oasis:names:tc:xacml:2.0:resource:resource-id"
ROLE = "urn:oasis:names:tc:xacml:2.0:subject:role"
PURPOSE = "urn:oasis:names:tc:xspa:1.0:subject:purposeofuse"
NS = {"s": SOAP_NS, "a": WSA_NS, "saml": SAML, "wsse": WSSE, "ds": DS, "query": QUERY_NS, "rim": RIM_NS}


def parse_xml(content: bytes) -> ET._Element:
    """Keep namespaces/signatures intact and refuse DTDs or entity expansion."""
    root = ET.fromstring(content, ET.XMLParser(resolve_entities=False, load_dtd=False, no_network=True))
    if root.getroottree().docinfo.doctype:
        raise ValueError("DOCTYPE is not allowed")
    return root


@dataclass(frozen=True)
class DiscoveryContext:
    """One verified request. Deployment switches live in config, not here.

    assertion is SignXML's verified result, never the unchecked input element.
    envelope retains the request body and addressing for routing/correlation.
    The signed resource claim binds the patient for discovery AND retrieval.
    """

    nhs_number: str
    message_id: str
    saml: SAMLAttributes
    assertion: ET._Element
    envelope: ET._Element
    expires_at: datetime


def verify_incoming(content: bytes, config: NIRDiscoveryConfig, action: str) -> DiscoveryContext:
    """Verify before PDS/cache/provider work; report a safe SOAP security fault.

    A configured certificate pins the upstream issuer. Issuer text alone, or
    trusting a certificate supplied inside the request, would not authenticate
    the claims. Reject duplicate XML IDs and ambiguous SOAP/assertion structure
    before selecting any signed data to avoid signature-wrapping ambiguity.
    """
    message_id = None
    try:
        root = parse_xml(content)
        if (
            root.tag != f"{{{SOAP_NS}}}Envelope"
            or len(root.findall("s:Header", NS)) != 1
            or len(root.findall("s:Body", NS)) != 1
        ):
            raise ValueError("Expected one SOAP header and body")
        for path in ("s:Header/a:MessageID", "s:Header/a:Action"):
            nodes = root.findall(path, NS)
            if len(nodes) != 1 or not (nodes[0].text or "").strip():
                raise ValueError("Missing or duplicated addressing")
        message_id = root.findtext("s:Header/a:MessageID", namespaces=NS)
        if len(message_id) > 256 or root.findtext("s:Header/a:Action", namespaces=NS) != action:
            raise ValueError("Invalid addressing")
        assertions = root.findall(".//saml:Assertion", NS)
        expected = root.findall("s:Header/wsse:Security/saml:Assertion", NS)
        ids = [
            value
            for node in root.iter()
            for name, value in node.attrib.items()
            if ET.QName(name).localname in {"ID", "Id", "id"}
        ]
        if len(assertions) != 1 or len(expected) != 1 or len(ids) != len(set(ids)):
            raise ValueError("Ambiguous assertion or XML IDs")
        now = datetime.now(UTC)
        cert = config.inbound_certificate
        if not cert.not_valid_before_utc <= now < cert.not_valid_after_utc:
            raise ValueError("Upstream certificate expired")
        verified = (
            XMLVerifier()
            .verify(
                expected[0],
                x509_cert=cert.public_bytes(serialization.Encoding.PEM),
                expect_config=SignatureConfiguration(
                    location="./",
                    expect_references=1,
                    signature_methods=frozenset({SignatureMethod.RSA_SHA256}),
                    digest_algorithms=frozenset({DigestAlgorithm.SHA256}),
                ),
            )
            .signed_xml
        )
        if verified.tag != f"{{{SAML}}}Assertion" or verified.get("Version") != "2.0":
            raise ValueError("Expected SAML 2.0")
        # Restrict the profile explicitly instead of selecting the first of
        # several conflicting subjects, condition sets or authentication events.
        for name in ("Issuer", "Subject", "Conditions", "AuthnStatement", "AttributeStatement"):
            if len(verified.findall(f"saml:{name}", NS)) != 1:
                raise ValueError("Missing or ambiguous assertion context")
        if verified.findtext("saml:Issuer", namespaces=NS) != config.inbound_issuer:
            raise ValueError("Unexpected issuer")
        conditions = verified.find("saml:Conditions", NS)
        restrictions = conditions.findall("saml:AudienceRestriction", NS)
        if not restrictions or any(
            config.inbound_audience not in r.xpath("saml:Audience/text()", namespaces=NS) for r in restrictions
        ):
            raise ValueError("Unexpected audience")
        if any(child.tag != f"{{{SAML}}}AudienceRestriction" for child in conditions):
            raise ValueError("Unsupported assertion condition")
        start = datetime.fromisoformat(conditions.get("NotBefore"))
        expiry = datetime.fromisoformat(conditions.get("NotOnOrAfter"))
        issued = datetime.fromisoformat(verified.get("IssueInstant"))
        if not start <= now < expiry or issued > now:
            raise ValueError("Assertion outside its validity window")
        subject = verified.find("saml:Subject", NS)
        if (
            len(subject.findall("saml:NameID", NS)) != 1
            or not (subject.findtext("saml:NameID", namespaces=NS) or "").strip()
        ):
            raise ValueError("Missing subject identity")
        confirmations = subject.findall("saml:SubjectConfirmation", NS)
        if len(confirmations) != 1 or confirmations[0].get("Method") != "urn:oasis:names:tc:SAML:2.0:cm:bearer":
            raise ValueError("Expected one bearer confirmation")
        data = confirmations[0].find("saml:SubjectConfirmationData", NS)
        if data is None:
            raise ValueError("Missing bearer data")
        if data.get("NotBefore") and now < datetime.fromisoformat(data.get("NotBefore")):
            raise ValueError("Bearer is not yet valid")
        if data.get("NotOnOrAfter"):
            expiry = min(expiry, datetime.fromisoformat(data.get("NotOnOrAfter")))
            if now >= expiry:
                raise ValueError("Bearer expired")
        if data.get("Recipient") and data.get("Recipient") != root.findtext("s:Header/a:To", namespaces=NS):
            raise ValueError("Unexpected bearer recipient")
        auth = verified.find("saml:AuthnStatement", NS)
        if datetime.fromisoformat(auth.get("AuthnInstant")) > now:
            raise ValueError("Authentication is in the future")
        if not (auth.findtext("saml:AuthnContext/saml:AuthnContextClassRef", namespaces=NS) or "").strip():
            raise ValueError("Missing authentication method")
        if auth.get("SessionNotOnOrAfter"):
            expiry = min(expiry, datetime.fromisoformat(auth.get("SessionNotOnOrAfter")))
            if now >= expiry:
                raise ValueError("Authentication session expired")

        # Use verified values for BOTH audit and outbound issuance. Normalise
        # the legacy home-community name for our existing audit model only;
        # outbound SAML uses the NIR attribute name and preserves coded XML.
        attrs = deepcopy(verified.find("saml:AttributeStatement", NS))
        names = [a.get("Name") for a in attrs]
        if len(names) != len(set(names)) or (HOME in names and LEGACY_HOME in names):
            raise ValueError("Duplicated claims")
        if any(len(attr.findall("saml:AttributeValue", NS)) != 1 for attr in attrs):
            raise ValueError("Expected one value per claim")
        claims = {a.get("Name"): a.find("saml:AttributeValue", NS) for a in attrs}
        for name in (ROLE, PURPOSE, RESOURCE):
            if name not in claims or claims[name] is None:
                raise ValueError("Missing required claim")
        home = claims.get(HOME) if HOME in claims else claims.get(LEGACY_HOME)
        if home is None or not (home.text or "").startswith("urn:oid:"):
            raise ValueError("Missing originating community")
        resource = claims[RESOURCE].text or ""
        patient = resource.split("^^^")[0]
        if resource != f"{patient}^^^&{NHS_NUMBER_ROOT}&ISO":
            raise ValueError("Invalid signed patient identifier")
        build_iti38_request(patient, config.client.base_url)  # Existing NHS checksum validation.
        for attr in attrs:
            if attr.get("Name") == HOME:
                attr.set("Name", LEGACY_HOME)
        if SUBJECT not in names:
            attr = ET.SubElement(attrs, f"{{{SAML}}}Attribute", Name=SUBJECT)
            ET.SubElement(attr, f"{{{SAML}}}AttributeValue").text = subject.findtext("saml:NameID", namespaces=NS)
        decoded = xmltodict.parse(
            ET.tostring(attrs),
            process_namespaces=True,
            namespaces={SAML: None, HL7: None, "http://www.w3.org/2001/XMLSchema-instance": "xsi"},
        )
        saml = process_saml_attributes(decoded["AttributeStatement"])
        validate_saml_attributes(saml)
        if not saml.role.code or not saml.purpose_of_use or not saml.purpose_of_use.code:
            raise ValueError("Role and purpose must be coded")
        return DiscoveryContext(patient, message_id, saml, verified, root, expiry)
    except Exception as exc:
        # Never put certificate material, patient values, or signature-library
        # diagnostics into a caller-visible fault. Cancellation is not caught.
        raise SoapFault(
            "Invalid NIR caller security context", subcode="wsse:FailedAuthentication", message_id=message_id
        ) from exc


def sign_outgoing(envelope: bytes, context: DiscoveryContext, config: NIRDiscoveryConfig) -> bytes:
    """Reissue verified claims, keeping the original authentication event.

    Only the assertion is signed, as in Q23. The SOAP body is protected by the
    authenticated transport; a detached message signature is a separate option.
    Sign in the final envelope so namespace changes cannot break the signature.
    """
    now = datetime.now(UTC)
    expiry = min(context.expires_at, now + timedelta(minutes=5), config.signing_certificate.not_valid_after_utc)
    if not config.signing_certificate.not_valid_before_utc <= now < expiry:
        raise SoapFault(
            "NIR signing context has expired", subcode="wsse:FailedAuthentication", message_id=context.message_id
        )
    # Preserve verified coded claims and their namespace declarations. Filtering
    # and the home-community alias are the only changes to the copied statement.
    attrs = deepcopy(context.assertion.find("saml:AttributeStatement", NS))
    allowed = {
        SUBJECT,
        RESOURCE,
        ROLE,
        PURPOSE,
        HOME,
        LEGACY_HOME,
        "urn:oasis:names:tc:xspa:1.0:subject:organization",
        "urn:oasis:names:tc:xspa:1.0:subject:organization-id",
    }
    for attr in list(attrs):
        if attr.get("Name") not in allowed:
            attrs.remove(attr)
            continue
        if attr.get("Name") == LEGACY_HOME:
            attr.set("Name", HOME)
        attr.set("NameFormat", "urn:oasis:names:tc:SAML:2.0:attrname-format:uri")

    assertion_model = SAMLAssertion(
        issuer=config.issuer,
        issue_instant=now.isoformat(),
        subject=SAMLSubject(
            name_id=context.assertion.find("saml:Subject/saml:NameID", NS),
            confirmation=BearerConfirmation(data=BearerConfirmationData(not_on_or_after=expiry.isoformat())),
        ),
        conditions=SAMLConditions(
            not_before=now.isoformat(),
            not_on_or_after=expiry.isoformat(),
            restriction=AudienceRestriction(audience=config.client.base_url.rstrip("/")),
        ),
        authentication=context.assertion.find("saml:AuthnStatement", NS),
        attributes=attrs,
    )
    root = parse_xml(envelope)
    security = WSSecurity(assertion=assertion_model).to_element()
    root.find("s:Header", NS).append(security)
    assertion = security.find("saml:Assertion", NS)
    # Sign only after assembly in the destination envelope. Never send the
    # signed element through Pydantic/xmltodict or pretty-print it afterwards.
    signed = XMLSigner(signature_algorithm="rsa-sha256", digest_algorithm="sha256", c14n_algorithm=C14N).sign(
        assertion,
        key=config.signing_key,
        cert=config.signing_certificate.public_bytes(serialization.Encoding.PEM),
        reference_uri="#" + assertion.get("ID"),
    )
    security.replace(assertion, signed)
    return ET.tostring(root, xml_declaration=True, encoding="UTF-8")
