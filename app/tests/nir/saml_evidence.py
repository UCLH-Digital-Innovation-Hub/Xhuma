"""Synthetic Q23 issuer harness. Not wired into Xhuma's inbound FastAPI routes."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from lxml import etree as ET
from signxml import DigestAlgorithm, SignatureConfiguration, SignatureMethod, XMLSigner, XMLVerifier

from app.soap.nir_queries import RIM_NS, SOAP_NS, WSA_NS, build_iti38_request

SAML = "urn:oasis:names:tc:SAML:2.0:assertion"
WSSE = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
DS = "http://www.w3.org/2000/09/xmldsig#"
C14N = "http://www.w3.org/2001/10/xml-exc-c14n#"
UPSTREAM_ISSUER = "urn:xhuma:test:upstream-issuer"
XHUMA_ISSUER = "urn:xhuma:test:assertion-issuer"
XHUMA_AUDIENCE = "https://xhuma.example.test"
ROLE = "urn:oasis:names:tc:xacml:2.0:subject:role"
PURPOSE = "urn:oasis:names:tc:xspa:1.0:subject:purposeofuse"
HOME = "urn:ihe:iti:xca:2010:homeCommunityId"
ATTRIBUTES = (
    ROLE,
    PURPOSE,
    HOME,
    "urn:oasis:names:tc:xspa:1.0:subject:organization",
    "urn:oasis:names:tc:xspa:1.0:subject:organization-id",
)
NS = {"s": SOAP_NS, "saml": SAML, "wsse": WSSE, "ds": DS, "rim": RIM_NS, "a": WSA_NS}


def timestamp(value):
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def test_certificate(name, now):
    """Return an ephemeral private key and public certificate; never persist the key."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return key, cert.public_bytes(serialization.Encoding.PEM)


def element(parent, name, text=None, **attrs):
    child = ET.SubElement(parent, f"{{{SAML}}}{name}", **attrs)
    child.text = text
    return child


def assertion(issuer, audience, now, context=None):
    root = ET.Element(
        f"{{{SAML}}}Assertion",
        nsmap={"saml": SAML, "ds": DS},
        ID="_" + uuid4().hex,
        Version="2.0",
        IssueInstant=timestamp(now),
    )
    element(root, "Issuer", issuer)
    ET.SubElement(root, f"{{{DS}}}Signature", Id="placeholder")
    subject = element(root, "Subject")
    if context is None:
        element(
            subject,
            "NameID",
            "test.clinician@example.test",
            Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
        )
    else:
        subject.append(deepcopy(context.find("saml:Subject/saml:NameID", NS)))
    confirmation = element(subject, "SubjectConfirmation", Method="urn:oasis:names:tc:SAML:2.0:cm:bearer")
    element(confirmation, "SubjectConfirmationData")
    expiry = now + timedelta(minutes=5)
    if context is not None:
        expiry = min(expiry, datetime.fromisoformat(context.find("saml:Conditions", NS).get("NotOnOrAfter")))
    conditions = element(root, "Conditions", NotBefore=timestamp(now), NotOnOrAfter=timestamp(expiry))
    element(element(conditions, "AudienceRestriction"), "Audience", audience)
    if context is None:
        auth = element(root, "AuthnStatement", AuthnInstant=timestamp(now - timedelta(minutes=2)))
        element(
            element(auth, "AuthnContext"),
            "AuthnContextClassRef",
            "urn:oasis:names:tc:SAML:2.0:ac:classes:PasswordProtectedTransport",
        )
        attrs = element(root, "AttributeStatement")
        for name, value in zip(
            ATTRIBUTES,
            (
                "DOCTOR",
                "TREATMENT",
                "urn:oid:1.2.826.0.1.3680043.10.999",
                "Synthetic Test Organisation",
                "urn:oid:1.2.826.0.1.3680043.10.999",
            ),
        ):
            attr = element(attrs, "Attribute", Name=name, NameFormat="urn:oasis:names:tc:SAML:2.0:attrname-format:uri")
            element(attr, "AttributeValue", value)
    else:
        root.append(deepcopy(context.find("saml:AuthnStatement", NS)))
        attrs = element(root, "AttributeStatement")
        for attr in context.findall("saml:AttributeStatement/saml:Attribute", NS):
            if attr.get("Name") in ATTRIBUTES:
                attrs.append(deepcopy(attr))
    return root


def attach_signed(envelope, assertion_xml, key, cert):
    header = envelope.find("s:Header", NS)
    security = ET.SubElement(header, f"{{{WSSE}}}Security", nsmap={"wsse": WSSE})
    security.append(assertion_xml)
    signed = XMLSigner(signature_algorithm="rsa-sha256", digest_algorithm="sha256", c14n_algorithm=C14N).sign(
        assertion_xml, key=key, cert=cert, reference_uri="#" + assertion_xml.get("ID")
    )
    security.replace(assertion_xml, signed)
    return ET.tostring(envelope, xml_declaration=True, encoding="UTF-8")


def parse_xml(content):
    root = ET.fromstring(content, ET.XMLParser(resolve_entities=False, load_dtd=False, no_network=True))
    if root.getroottree().docinfo.doctype:
        raise ValueError("DOCTYPE is not allowed")
    return root


def verify_context(envelope, cert, issuer, audience, now):
    root = parse_xml(envelope)
    assertions = root.findall(".//saml:Assertion", NS)
    expected = root.findall("s:Header/wsse:Security/saml:Assertion", NS)
    ids = [
        value
        for node in root.iter()
        for name, value in node.attrib.items()
        if ET.QName(name).localname in ("ID", "Id", "id")
    ]
    if len(assertions) != 1 or len(expected) != 1 or len(ids) != len(set(ids)):
        raise ValueError("Expected one header assertion and unique XML IDs")
    verified = (
        XMLVerifier()
        .verify(
            expected[0],
            x509_cert=cert,
            expect_config=SignatureConfiguration(
                location="./",
                signature_methods=frozenset({SignatureMethod.RSA_SHA256}),
                digest_algorithms=frozenset({DigestAlgorithm.SHA256}),
                expect_references=1,
            ),
        )
        .signed_xml
    )
    if verified.tag != f"{{{SAML}}}Assertion" or verified.get("Version") != "2.0":
        raise ValueError("Expected SAML 2.0 assertion")
    if verified.findtext("saml:Issuer", namespaces=NS) != issuer:
        raise ValueError("Unexpected issuer")
    if verified.findall("saml:Conditions/saml:AudienceRestriction/saml:Audience", NS)[0].text != audience:
        raise ValueError("Unexpected audience")
    conditions = verified.find("saml:Conditions", NS)
    if (
        not datetime.fromisoformat(conditions.get("NotBefore"))
        <= now
        < datetime.fromisoformat(conditions.get("NotOnOrAfter"))
    ):
        raise ValueError("Assertion is not currently valid")
    for path in ("saml:Subject/saml:NameID", "saml:AuthnStatement/saml:AuthnContext/saml:AuthnContextClassRef"):
        if not (verified.findtext(path, namespaces=NS) or "").strip():
            raise ValueError("Missing identity or authentication context")
    confirmation = verified.find("saml:Subject/saml:SubjectConfirmation", NS)
    if (
        confirmation is None
        or confirmation.get("Method") != "urn:oasis:names:tc:SAML:2.0:cm:bearer"
        or confirmation.find("saml:SubjectConfirmationData", NS) is None
    ):
        raise ValueError("Missing bearer confirmation")
    auth_time = datetime.fromisoformat(verified.find("saml:AuthnStatement", NS).get("AuthnInstant"))
    if auth_time > now:
        raise ValueError("Authentication is in the future")
    for name in (ROLE, PURPOSE, HOME):
        attrs = verified.xpath("saml:AttributeStatement/saml:Attribute[@Name=$name]", namespaces=NS, name=name)
        if len(attrs) != 1 or not "".join(attrs[0].itertext()).strip():
            raise ValueError("Missing or duplicated required claim")
    return verified


def synthetic_incoming(key, cert, now, patient="9694116422"):
    root = parse_xml(build_iti38_request(patient, XHUMA_AUDIENCE + "/SOAP/iti38"))
    return attach_signed(root, assertion(UPSTREAM_ISSUER, XHUMA_AUDIENCE, now), key, cert)


def reissue_for_nir(incoming, upstream_cert, key, cert, endpoint, audience, now):
    """Harness assumes authenticated upstream transport; assertion alone does not sign the body."""
    context = verify_context(incoming, upstream_cert, UPSTREAM_ISSUER, XHUMA_AUDIENCE, now)
    root = parse_xml(incoming)
    values = root.xpath(
        "s:Body/*/rim:AdhocQuery/rim:Slot[@name='$XDSDocumentEntryPatientId']/rim:ValueList/rim:Value", namespaces=NS
    )
    if len(values) != 1:
        raise ValueError("Expected one patient identifier")
    value = values[0].text or ""
    patient = value.strip("'").split("^^^")[0]
    if value != f"'{patient}^^^&2.16.840.1.113883.2.1.4.1&ISO'":
        raise ValueError("Unexpected patient assigning authority")
    outgoing = parse_xml(build_iti38_request(patient, endpoint))
    return patient, attach_signed(outgoing, assertion(XHUMA_ISSUER, audience, now, context), key, cert)


def reuse_nir_assertion(envelope, signed_source, cert, audience, now):
    """Reuse an unexpired assertion without modifying its signature or claims."""
    verify_context(signed_source, cert, XHUMA_ISSUER, audience, now)
    source = parse_xml(signed_source).find("s:Header/wsse:Security/saml:Assertion", NS)
    root = parse_xml(envelope)
    header = root.find("s:Header", NS)
    if header.find("wsse:Security", NS) is not None:
        raise ValueError("Destination envelope already contains security")
    security = ET.SubElement(header, f"{{{WSSE}}}Security", nsmap={"wsse": WSSE})
    security.append(deepcopy(source))
    outgoing = ET.tostring(root, xml_declaration=True, encoding="UTF-8")
    verify_context(outgoing, cert, XHUMA_ISSUER, audience, now)
    return outgoing
