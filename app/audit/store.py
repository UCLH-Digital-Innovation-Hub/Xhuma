from __future__ import annotations

from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from .db_models import AuditEventRow
from .models import AuditEvent


def _role_code(evt: AuditEvent) -> Optional[str]:
    rp = evt.role_profile or {}
    return rp.get("@code") or rp.get("code")


def _role_name(evt: AuditEvent) -> Optional[str]:
    rp = evt.role_profile or {}
    return rp.get("@displayName") or rp.get("displayName") or rp.get("display")


def _purpose_of_use_name(evt: AuditEvent) -> Optional[str]:
    pou = evt.purpose_of_use or {}
    return pou.get("@displayName") or pou.get("displayName") or pou.get("display")


async def insert_audit_event(session: AsyncSession, evt: AuditEvent) -> None:
    if not evt.subject_ref:
        raise ValueError("AuditEvent.subject_ref is None (missing API_KEY or nhs number).")

    row = AuditEventRow(
        audit_id=evt.audit_id,
        sequence=evt.sequence,
        event_time=evt.event_time,
        organisation=evt.organisation,
        request_id=evt.request_id,
        trace_id=evt.trace_id,
        user_id=evt.user_id,
        user_role_code=_role_code(evt),
        user_role_name=_role_name(evt),
        user_org_name=evt.saml.organization,
        user_org_id=evt.saml.organization_id,
        purpose_of_use=_purpose_of_use_name(evt),
        action=evt.event.action,
        outcome=evt.event.outcome.value,
        error_code=evt.event.error_code,
        subject_ref=evt.subject_ref,
        message_id=evt.event.data_refs.message_id,
        document_id=evt.event.data_refs.document_id,
        client_ip=evt.device.ip if evt.device else None,
        user_agent=evt.device.user_agent if evt.device else None,
        detail=evt.event.detail,
    )
    session.add(row)
