from __future__ import annotations

from datetime import datetime
from typing import Any
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

    organisation: str | None = Field(default=None, index=True)

    request_id: str | None = Field(default=None, index=True)
    trace_id: str | None = Field(default=None, index=True)

    user_id: str | None = Field(default=None, index=True)
    user_role_code: str | None = Field(default=None)
    user_role_name: str | None = Field(default=None)

    user_org_name: str | None = Field(default=None)
    user_org_id: str | None = Field(default=None)

    purpose_of_use: str | None = Field(default=None)

    action: str = Field(index=True)
    outcome: str = Field(index=True)
    error_code: str | None = Field(default=None)

    subject_ref: str = Field(index=True)

    message_id: str | None = Field(default=None)
    document_id: str | None = Field(default=None)

    client_ip: str | None = Field(default=None)
    user_agent: str | None = Field(default=None)

    # Keep original event.detail structure
    detail: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
