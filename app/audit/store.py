from __future__ import annotations

from typing import Any, Optional

from .models import AuditEvent

# 21 columns -> 21 placeholders ($1..$21)
INSERT_SQL = """
INSERT INTO audit_event (
  audit_id, sequence, event_time,
  organisation,
  request_id, trace_id,
  user_id,
  user_role_code, user_role_name,
  user_org_name, user_org_id, urp_id,
  action, outcome, error_code,
  subject_ref, message_id, document_id,
  client_ip, user_agent,
  detail
)
VALUES (
  $1,$2,$3,
  $4,
  $5,$6,
  $7,
  $8,$9,
  $10,$11,$12,
  $13,$14,$15,
  $16,$17,$18,
  $19,$20,
  $21
)
"""


def _role_code(evt: AuditEvent) -> Optional[str]:
    rp = evt.role_profile or {}
    return rp.get("@code") or rp.get("code")


def _role_name(evt: AuditEvent) -> Optional[str]:
    rp = evt.role_profile or {}
    return rp.get("@displayName") or rp.get("displayName") or rp.get("display")

# def _purpose(evt: AuditEvent) -> Optional[str]:
#     rp = evt.saml.purpose_of_use or {}
#     return rp.get("@displayName") or rp.get("displayName") or rp.get("display")


async def insert_audit_event(pg: Any, evt: AuditEvent) -> None:
    """
    Persist an AuditEvent into Postgres using an asyncpg-style pool.
    """
    async with pg.acquire() as conn:
        await conn.execute(
            INSERT_SQL,
            evt.audit_id,                         # $1
            evt.sequence,                         # $2
            evt.event_time,                       # $3
            evt.organisation,                     # $4 
            evt.request_id,                       # $5
            evt.trace_id,                         # $6
            evt.user_id,                          # $7 
            _role_code(evt),                      # $8
            _role_name(evt),                      # $9
            evt.saml.organization,                # $10
            evt.saml.organization_id,             # $11
            evt.saml.purpose_of_use.displayName,  # $12 purpose of use
            evt.event.action,                     # $13
            evt.event.outcome.value,              # $14
            evt.event.error_code,                 # $15
            evt.subject_ref,                      # $16 (computed HMAC pseudonym)
            evt.event.data_refs.message_id,       # $17
            evt.event.data_refs.document_id,      # $18
            evt.device.ip if evt.device else None,                # $19
            evt.device.user_agent if evt.device else None,        # $20
            evt.event.detail,                     # $21 (dict -> json/jsonb)
        )
