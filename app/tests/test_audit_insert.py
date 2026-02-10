import uuid
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
import xmltodict

from app.audit.audit import process_saml_attributes
from app.audit.db_models import AuditEventRow
from app.audit.models import (
    AuditEvent,
    AuditEventDetail,
    AuditOutcome,
    DeviceInfo,
    EventDataRefs,
)
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
async def test_insert_audit_event_adds_expected_row(monkeypatch):
    monkeypatch.setenv("API_KEY", "unit-test-secret")

    session = Mock()
    session.add = Mock()

    saml = saml_from_xml(xml39)

    evt = AuditEvent(
        sequence=42,
        subject_nhs_number="9690937278",
        event_time=datetime(2026, 2, 2, 12, 0, 0, tzinfo=timezone.utc),
        organisation="RRV00",
        request_id="req-123",
        trace_id="trace-abc",
        saml=saml,
        device=DeviceInfo(ip="127.0.0.1", user_agent="pytest", host="testserver"),
        event=AuditEventDetail(
            action="gpc.getstructuredrecord",
            outcome=AuditOutcome.ok,
            error_code=None,
            data_refs=EventDataRefs(message_id="msg-1", document_id="doc-1"),
            detail={"k": "v"},
        ),
    )

    assert evt.subject_ref is not None
    assert evt.subject_ref.startswith("v1:")
    assert "9690937278" not in evt.subject_ref

    await insert_audit_event(session, evt)

    session.add.assert_called_once()
    (row,) = session.add.call_args.args
    assert isinstance(row, AuditEventRow)

    assert row.audit_id == evt.audit_id
    assert row.sequence == evt.sequence
    assert row.event_time == evt.event_time
    assert row.organisation == evt.organisation

    assert row.request_id == evt.request_id
    assert row.trace_id == evt.trace_id

    assert row.user_id == evt.user_id
    assert row.user_role_code == (evt.saml.role.code if evt.saml.role else None)
    assert row.user_role_name == (evt.saml.role.displayName if evt.saml.role else None)
    assert row.user_org_name == evt.saml.organization
    assert row.user_org_id == evt.saml.organization_id

    assert row.purpose_of_use == (
        evt.saml.purpose_of_use.displayName if evt.saml.purpose_of_use else None
    )

    assert row.action == evt.event.action
    assert row.outcome == evt.event.outcome.value
    assert row.error_code == evt.event.error_code

    assert row.subject_ref == evt.subject_ref

    assert row.message_id == evt.event.data_refs.message_id
    assert row.document_id == evt.event.data_refs.document_id

    assert row.client_ip == evt.device.ip
    assert row.user_agent == evt.device.user_agent

    assert row.detail == evt.event.detail
