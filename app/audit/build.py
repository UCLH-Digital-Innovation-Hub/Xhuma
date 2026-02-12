from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import Request
from opentelemetry import trace
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.sequence import next_audit_sequence

from .models import (
    AuditEvent,
    AuditEventDetail,
    AuditOutcome,
    AuthorityIdentity,
    DeviceInfo,
    EventDataRefs,
    SAMLAttributes,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _client_ip(request: Request) -> Optional[str]:
    return request.headers.get("x-forwarded-for") or (
        request.client.host if request.client else None
    )


def _trace_id() -> Optional[str]:
    span = trace.get_current_span()
    ctx = span.get_span_context() if span else None
    if not ctx or not ctx.is_valid:
        return None
    return format(ctx.trace_id, "032x")


async def build_audit_event(
    *,
    request: Request,
    session: AsyncSession,
    saml: SAMLAttributes,
    nhs_number: str,
    action: str,
    outcome: AuditOutcome,
    # optional extra refs
    message_id: Optional[str] = None,
    document_id: Optional[str] = None,
    # failure context
    error_code: Optional[str] = None,
    detail: Optional[Dict[str, Any]] = None,
    request_id: Optional[str] = None,
) -> AuditEvent:
    """
    Build a validated AuditEvent (Pydantic).

    Design choice:
    - SAML model describes the *user/session*.
    - Patient/subject identity is passed separately as subject_ref.
    """
    seq = await next_audit_sequence(session)

    evt = AuditEvent(
        # audit_id=uuid.uuid4(),
        sequence=seq,
        subject_nhs_number=nhs_number,
        event_time=_utcnow(),
        # service_name=os.getenv("OTEL_SERVICE_NAME", "xhuma"),
        organisation=os.getenv("ORG_CODE", "RRV00"),
        request_id=request_id or request.headers.get("x-request-id"),
        trace_id=_trace_id(),
        saml=saml,
        device=DeviceInfo(
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
            host=request.headers.get("host"),
        ),
        event=AuditEventDetail(
            action=action,
            outcome=outcome,
            error_code=error_code,
            data_refs=EventDataRefs(
                # subject_ref=subject_ref,
                message_id=message_id,
                document_id=document_id,
            ),
            detail=detail or {},
        ),
    )
    return evt
