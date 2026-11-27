from datetime import UTC, datetime, timedelta

import pytest
from lxml import etree
from signxml import InvalidSignature

from app.soap.nir_queries import build_iti38_request
from app.tests.fixtures.saml_attributes import saml

from .saml_evidence import (
    HOME,
    NS,
    PURPOSE,
    RESOURCE,
    ROLE,
    SUBJECT,
    UPSTREAM_ISSUER,
    XHUMA_AUDIENCE,
    XHUMA_ISSUER,
    assertion,
    attach_signed,
    parse_xml,
    reissue_for_nir,
    synthetic_incoming,
    verify_context,
)
from .saml_evidence import (
    test_certificate as make_certificate,
)


@pytest.fixture
def signed_request():
    now = datetime.now(UTC)
    key, cert = make_certificate("upstream synthetic", now)
    return now, key, cert, synthetic_incoming(key, cert, now)


def test_reissues_with_new_identity_audience_and_signature(signed_request):
    now, _, cert, incoming = signed_request
    key, outgoing_cert = make_certificate("outgoing synthetic", now)
    patient, outgoing = reissue_for_nir(
        incoming, cert, key, outgoing_cert, "https://sandbox.example/xca/iti-38", "https://sandbox.example", now
    )
    verified = verify_context(outgoing, outgoing_cert, XHUMA_ISSUER, "https://sandbox.example", now)
    assert patient == "9694116422"
    assert verified.findtext("saml:Subject/saml:NameID", namespaces=NS) == saml.subject_id
    assert verified.find("saml:AuthnStatement", NS).get("AuthnInstant") == verify_context(
        incoming, cert, UPSTREAM_ISSUER, XHUMA_AUDIENCE, now
    ).find("saml:AuthnStatement", NS).get("AuthnInstant")


@pytest.mark.parametrize("patient", ["9694116422", "9932549118", "9690937278"])
def test_shared_fixture_claims_survive_signed_reissue(signed_request, patient):
    now, upstream_key, upstream_cert, _ = signed_request
    incoming = synthetic_incoming(upstream_key, upstream_cert, now, patient=patient)
    key, cert = make_certificate("outgoing synthetic", now)
    actual_patient, outgoing = reissue_for_nir(
        incoming, upstream_cert, key, cert, "https://sandbox.example/xca/iti-38", "https://sandbox.example", now
    )
    verified = verify_context(outgoing, cert, XHUMA_ISSUER, "https://sandbox.example", now)
    assert actual_patient == patient
    assert verified.find("saml:Subject/saml:NameID", NS).get("Format").endswith(":unspecified")
    values = {
        attr.get("Name"): attr.find("saml:AttributeValue", NS)
        for attr in verified.findall("saml:AttributeStatement/saml:Attribute", NS)
    }
    assert {name: value.text for name, value in values.items() if name not in (ROLE, PURPOSE)} == {
        SUBJECT: saml.subject_id,
        HOME: saml.home_community_id,
        "urn:oasis:names:tc:xspa:1.0:subject:organization": saml.organization,
        "urn:oasis:names:tc:xspa:1.0:subject:organization-id": saml.organization_id,
        RESOURCE: f"{patient}^^^&2.16.840.1.113883.2.1.4.1&ISO",
    }
    for name, tag, expected in ((ROLE, "Role", saml.role), (PURPOSE, "PurposeForUse", saml.purpose_of_use)):
        coded = values[name].find(f"{{urn:hl7-org:v3}}{tag}")
        for attribute in ("code", "codeSystem", "codeSystemName", "displayName"):
            assert coded.get(attribute) == getattr(expected, attribute)
        assert coded.get("{http://www.w3.org/2001/XMLSchema-instance}type") == "CE"


@pytest.mark.parametrize(
    "failure", ["tampered", "tampered_role", "unknown_certificate", "expired", "audience", "duplicate"]
)
def test_rejects_invalid_incoming_assertion(signed_request, failure):
    now, _, cert, incoming = signed_request
    root = parse_xml(incoming)
    if failure == "tampered":
        root.find(".//saml:NameID", NS).text = "forged@example.test"
        incoming = etree.tostring(root)
    elif failure == "tampered_role":
        root.find(".//{urn:hl7-org:v3}Role").set("code", "forged")
        incoming = etree.tostring(root)
    elif failure == "unknown_certificate":
        _, cert = make_certificate("untrusted", now)
    elif failure == "expired":
        now += timedelta(minutes=6)
    elif failure == "duplicate":
        from copy import deepcopy

        security = root.find("s:Header/wsse:Security", NS)
        security.append(deepcopy(security[0]))
        incoming = etree.tostring(root)
    audience = "https://wrong.example" if failure == "audience" else XHUMA_AUDIENCE
    with pytest.raises((ValueError, InvalidSignature)):
        verify_context(incoming, cert, UPSTREAM_ISSUER, audience, now)


def test_signed_missing_required_claim_rejected(signed_request):
    now, key, cert, _ = signed_request
    claim = assertion(UPSTREAM_ISSUER, XHUMA_AUDIENCE, now)
    claim.remove(claim.find("saml:AttributeStatement", NS))
    incoming = attach_signed(parse_xml(build_iti38_request("9694116422", XHUMA_AUDIENCE)), claim, key, cert)
    with pytest.raises(ValueError, match="required claim"):
        verify_context(incoming, cert, UPSTREAM_ISSUER, XHUMA_AUDIENCE, now)


@pytest.mark.parametrize("tag", ["Role", "PurposeForUse"])
@pytest.mark.parametrize("code", [None, "", " "])
def test_signed_empty_coded_claim_rejected(signed_request, tag, code):
    now, key, cert, _ = signed_request
    claim = assertion(UPSTREAM_ISSUER, XHUMA_AUDIENCE, now)
    coded = claim.find(f".//{{urn:hl7-org:v3}}{tag}")
    if code is None:
        del coded.attrib["code"]
    else:
        coded.set("code", code)
    incoming = attach_signed(parse_xml(build_iti38_request("9694116422", XHUMA_AUDIENCE)), claim, key, cert)
    with pytest.raises(ValueError, match="required claim"):
        verify_context(incoming, cert, UPSTREAM_ISSUER, XHUMA_AUDIENCE, now)


def test_reuses_identical_assertion_with_fresh_addressing(signed_request):
    from app.soap.nir_models import NIRDocumentReference
    from app.soap.nir_queries import build_iti39_request

    from .saml_evidence import reuse_nir_assertion

    now, _, cert, incoming = signed_request
    key, outgoing_cert = make_certificate("outgoing synthetic", now)
    _, outgoing = reissue_for_nir(
        incoming, cert, key, outgoing_cert, "https://sandbox.example/xca/iti-38", "https://sandbox.example", now
    )
    reference = NIRDocumentReference(
        home_community_id="urn:oid:1.2.3", repository_unique_id="1.2.3", document_unique_id="1.2.4"
    )
    envelope = build_iti39_request([reference], "https://sandbox.example/xca/iti-39")
    reused = reuse_nir_assertion(envelope, outgoing, outgoing_cert, "https://sandbox.example", now)
    original, new = parse_xml(outgoing), parse_xml(reused)
    for path in (".//saml:Assertion", ".//ds:SignatureValue"):
        a, b = original.find(path, NS), new.find(path, NS)
        assert etree.tostring(a, method="c14n", exclusive=True) == etree.tostring(b, method="c14n", exclusive=True)
    assert original.findtext("s:Header/a:MessageID", namespaces=NS) != new.findtext(
        "s:Header/a:MessageID", namespaces=NS
    )
    assert new.findtext("s:Header/a:To", namespaces=NS) == "https://sandbox.example/xca/iti-39"
    with pytest.raises(ValueError, match="currently valid"):
        reuse_nir_assertion(envelope, outgoing, outgoing_cert, "https://sandbox.example", now + timedelta(minutes=6))
