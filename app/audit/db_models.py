from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import Column, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class AuditEventRow(SQLModel, table=True):
    """
    Flattened and DB-friendly; built from Pydantic AuditEvent.
    """

    __tablename__ = "audit_event"

    audit_id: UUID = Field(primary_key=True)
    sequence: int = Field(index=True)

    event_time: datetime = Field(
        sa_column=Column(DateTime(timezone=True), index=True),
    )

    organisation: Optional[str] = Field(default=None, index=True)

    request_id: Optional[str] = Field(default=None, index=True)
    trace_id: Optional[str] = Field(default=None, index=True)

    user_id: Optional[str] = Field(default=None, index=True)
    user_role_code: Optional[str] = Field(default=None)
    user_role_name: Optional[str] = Field(default=None)

    user_org_name: Optional[str] = Field(default=None)
    user_org_id: Optional[str] = Field(default=None)

    purpose_of_use: Optional[str] = Field(default=None)

    action: str = Field(index=True)
    outcome: str = Field(index=True)
    error_code: Optional[str] = Field(default=None)

    subject_ref: str = Field(index=True)

    message_id: Optional[str] = Field(default=None)
    document_id: Optional[str] = Field(default=None)

    client_ip: Optional[str] = Field(default=None)
    user_agent: Optional[str] = Field(default=None)

    # Keep original event.detail structure
    detail: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSONB))
