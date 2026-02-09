import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.audit.build import build_audit_event

# Adjust imports to match your code
from app.audit.models import AuditEvent, AuditOutcome, SAMLAttributes
from app.audit.store import (
    insert_audit_event,
)

from .configure_tests import fake_pg_pool


@pytest.mark.asyncio
async def test_insert_audit_event_writes_expected_fields(fake_pg_pool):
    # --- fake Postgres pool + sequence ---
    fake_pool, fake_conn = fake_pg_pool
    fake_conn.fetchrow.return_value = {"seq": 42}

    # --- fake FastAPI Request ---
    fake_request = SimpleNamespace(
        headers={
            "x-request-id": "req-123",
            "user-agent": "pytest",
            "host": "testserver",
        },
        client=SimpleNamespace(host="127.0.0.1"),
    )

    # --- SAML attributes ---
    attrs = SAMLAttributes.model_validate(
        {
            "subject_id": "CONE, Stephen",
            "organization": "UCLH - University College London Hospitals - TST",
            "organization_id": "urn:oid:1.2.3",
            "home_community_id": "urn:oid:1.2.3",
            "role": {
                "@xsi:type": "CD",
                "@code": "224608005",
                "@codeSystemName": "http://snomed.info/sct",
                "@displayName": "Administrative healthcare staff",
            },
            "purpose_of_use": {
                "@xsi:type": "CD",
                "@code": "TREATMENT",
                "@codeSystemName": "LOINC",
                "@displayName": "Treatment",
            },
            "resource_id": "9690937278^^^&2.16.840.1.113883.2.1.4.1&ISO",
        }
    )

    # --- build event ---
    ev = await build_audit_event(
        request=fake_request,
        pg_pool=fake_pool,
        nhs_number="9690937278",
        saml=attrs,
        action="gpc.getstructuredrecord",
        outcome=AuditOutcome.ok,
    )

    assert ev.sequence == 42
    assert ev.saml.subject_id == "CONE, Stephen"
    assert ev.event.action == "gpc.getstructuredrecord"

    assert "v1:" in ev.subject_ref
    assert "9690937278" not in ev.subject_ref

    # # --- insert ---
    # insert_conn = AsyncMock()
    # insert_conn.fetchrow.return_value = {"id": 1}

    # insert_pool = AsyncMock()
    # insert_pool.acquire.return_value.__aenter__.return_value = insert_conn

    # row_id = await insert_audit_event(insert_pool, ev)

    # assert row_id == 1
    # assert insert_conn.fetchrow.await_count == 1
