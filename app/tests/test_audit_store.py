import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import xmltodict

from app.audit.audit import process_saml_attributes
from app.audit.build import build_audit_event

# Adjust imports to match your code
from app.audit.models import AuditEvent, AuditOutcome, SAMLAttributes
from app.audit.store import insert_audit_event

xml39 = '<AttributeStatement><Attribute Name="urn:oasis:names:tc:xspa:1.0:subject:subject-id"><AttributeValue>CONE, Stephen</AttributeValue></Attribute><Attribute Name="urn:oasis:names:tc:xspa:1.0:subject:organization"><AttributeValue>UCLH - University College London Hospitals - TST</AttributeValue></Attribute><Attribute Name="urn:oasis:names:tc:xspa:1.0:subject:organization-id"><AttributeValue>urn:oid:1.2.840.114350.1.13.525.3.7.3.688884.100</AttributeValue></Attribute><Attribute Name="urn:nhin:names:saml:homeCommunityId"><AttributeValue>urn:oid:1.2.840.114350.1.13.525.3.7.3.688884.100</AttributeValue></Attribute><Attribute Name="urn:oasis:names:tc:xacml:2.0:subject:role"><AttributeValue><Role xsi:type="CE" code="224608005" codeSystem="2.16.840.1.113883.6.96" codeSystemName="SNOMED_CT" displayName="Administrative healthcare staff" xmlns="urn:hl7-org:v3" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema"/></AttributeValue></Attribute><Attribute Name="urn:oasis:names:tc:xspa:1.0:subject:purposeofuse"><AttributeValue><PurposeForUse xsi:type="CE" code="TREATMENT" codeSystem="2.16.840.1.113883.3.18.7.1" codeSystemName="nhin-purpose" displayName="Treatment" xmlns="urn:hl7-org:v3" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema"/></AttributeValue></Attribute><Attribute Name="urn:oasis:names:tc:xacml:2.0:resource:resource-id"><AttributeValue>9690937278^^^&amp;2.16.840.1.113883.2.1.4.1&amp;ISO</AttributeValue></Attribute></AttributeStatement>'


def saml_from_xml(xml: str):
    # Ensure Attribute is always a list (your code requires a list)
    parsed = xmltodict.parse(xml, force_list=("Attribute",))

    stmt = parsed.get("AttributeStatement")
    if stmt is None:
        # fallback if xmltodict wraps differently
        stmt = next(iter(parsed.values()))

    return process_saml_attributes(stmt)


@pytest.mark.asyncio
async def test_build_audit_event_writes_expected_fields(monkeypatch):
    monkeypatch.setenv("API_KEY", "unit-test-secret")

    async def _fake_next_audit_sequence(_session):
        return 42

    monkeypatch.setattr(
        "app.audit.build.next_audit_sequence", _fake_next_audit_sequence
    )

    fake_session = object()

    fake_request = SimpleNamespace(
        headers={
            "x-request-id": "req-123",
            "user-agent": "pytest",
            "host": "testserver",
        },
        client=SimpleNamespace(host="127.0.0.1"),
    )

    saml = saml_from_xml(xml39)

    ev = await build_audit_event(
        request=fake_request,
        session=fake_session,
        nhs_number="9690937278",
        saml=saml,
        action="gpc.getstructuredrecord",
        outcome=AuditOutcome.ok,
    )

    assert ev.sequence == 42
    assert ev.saml.subject_id == "CONE, Stephen"
    assert ev.saml.organization == "UCLH - University College London Hospitals - TST"
    assert ev.event.action == "gpc.getstructuredrecord"

    assert ev.subject_ref is not None
    assert ev.subject_ref.startswith("v1:")
    assert "9690937278" not in ev.subject_ref
