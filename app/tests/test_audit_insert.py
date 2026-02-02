import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.audit.models import AuditEvent, AuditEventDetail, AuditOutcome, EventDataRefs, SAMLAttributes, DeviceInfo
from app.audit.store import insert_audit_event, INSERT_SQL


class _AcquireCM:
    """Async context manager returned by pg.acquire()."""
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_insert_audit_event_executes_expected_sql_and_args(monkeypatch):
    # Make subject_ref deterministic/present (AuditEvent.subject_ref uses env var API_KEY)
    monkeypatch.setenv("API_KEY", "unit-test-secret")

    # --- Fake PG pool/conn ---
    conn = AsyncMock()
    pg = SimpleNamespace(acquire=lambda: _AcquireCM(conn))

    # --- Build a minimal but fully-populated event ---
    evt = AuditEvent(
        audit_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        sequence=42,
        subject_nhs_number="9690937278",
        event_time=datetime(2026, 2, 2, 12, 0, 0, tzinfo=timezone.utc),
        organisation="RRV00",
        request_id="req-123",
        trace_id="trace-abc",
        saml=SAMLAttributes(
            subject_id="CONE, Stephen",
            organization="UCLH - University College London Hospitals - TST",
            organization_id="urn:oid:1.2.3",
            home_community_id="urn:oid:1.2.3",
            role={
                "@xsi:type": "CD",
                "@code": "224608005",
                "@codeSystemName": "http://snomed.info/sct",
                "@displayName": "Administrative healthcare staff",
            },
            purpose_of_use={
                "@xsi:type": "CD",
                "@code": "TREATMENT",
                "@codeSystemName": "LOINC",
                "@displayName": "Treatment",
            },
            resource_id="9690937278^^^&2.16.840.1.113883.2.1.4.1&ISO",
        ),
        device=DeviceInfo(ip="127.0.0.1", user_agent="pytest", host="testserver"),
        event=AuditEventDetail(
            action="gpc.getstructuredrecord",
            outcome=AuditOutcome.ok,
            error_code=None,
            data_refs=EventDataRefs(
                message_id="msg-1",
                document_id="doc-1",
            ),
            detail={"k": "v"},
        ),
    )

    # Sanity check: computed subject_ref exists and is pseudonymous
    assert evt.subject_ref is not None
    assert evt.subject_ref.startswith("v1:")
    assert "9690937278" not in evt.subject_ref

    # --- Act ---
    await insert_audit_event(pg, evt)

    # --- Assert: one execute with correct SQL + ordered args ---
    assert conn.execute.await_count == 1
    call = conn.execute.await_args
    sql = call.args[0]
    args = call.args[1:]

    assert sql.strip() == INSERT_SQL.strip()

    # Expected mapping (in the exact order of INSERT_SQL placeholders)
    expected = (
        evt.audit_id,                      # $1
        evt.sequence,                      # $2
        evt.event_time,                    # $3
        evt.organisation,                  # $4
        evt.request_id,                    # $5
        evt.trace_id,                      # $6
        evt.user_id,                       # $7 (computed from saml.subject_id)
        evt.role_profile.get("@code"),     # $8
        evt.role_profile.get("@displayName"),  # $9
        evt.saml.organization,             # $10
        evt.saml.organization_id,          # $11
        evt.saml.purpose_of_use.displayName,                              # $12 (urp_id) - not modeled currently
        evt.event.action,                  # $13
        evt.event.outcome.value,           # $14
        evt.event.error_code,              # $15
        evt.subject_ref,                   # $16 (computed pseudonymous subject ref)
        evt.event.data_refs.message_id,    # $17
        evt.event.data_refs.document_id,   # $18
        evt.device.ip,                     # $19
        evt.device.user_agent,             # $20
        evt.event.detail,                  # $21
    )

    assert args == expected
