import base64
import hashlib
import hmac
import os
import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, computed_field, field_validator

from ..ccda.models.datatypes import CD


def _subject_ref_from_nhs_number(nhs_number: str, secret: str, *, version: str = "v1") -> str:
    """
    HMAC-based, non-reversible pseudonym. Safe to store in logs/audit DB.
    Args:
        nhs_number (str): NHS number to store
        secret (str): Secret key for HMAC
        version (str, optional): Version string for future-proofing. Defaults to "v1".
    Returns:
        str: Pseudonym string for audit storage
    """
    mac = hmac.new(secret.encode("utf-8"), nhs_number.encode("utf-8"), hashlib.sha256).digest()
    short = mac[:18]  # 144-bit token
    token = base64.urlsafe_b64encode(short).decode("ascii").rstrip("=")
    return f"{version}:{token}"


# ---- Enums ----


class AuditOutcome(str, Enum):
    ok = "ok"
    fail = "fail"
    deny = "deny"


# ---- Sub-models ----


class SAMLAttributes(BaseModel):
    subject_id: str | None = None
    organization: str | None = None
    organization_id: str | None = None
    home_community_id: str | None = None

    role: CD | None = None
    purpose_of_use: CD | None = None

    # XACML resource-id (contains patient identifier)
    resource_id: str | None = None

    model_config = {"extra": "forbid"}


class OrganisationRef(BaseModel):
    name: str | None = None
    id: str | None = None
    home_community_id: str | None = None


class UserIdentity(BaseModel):
    user_id: str | None = None
    name: str | None = None
    role_profile: CD | None = None
    organisation: OrganisationRef | None = None
    urp_id: str | None = None
    purpose_of_use: dict[str, Any] | None = None  # keep structured


class AuthorityIdentity(BaseModel):
    id: str | None = None
    name: str | None = None


class DeviceInfo(BaseModel):
    ip: str | None = None
    user_agent: str | None = None
    host: str | None = None


class EventDataRefs(BaseModel):
    # subject_ref: Optional[str]
    message_id: str | None = None
    document_id: str | None = None


class AuditEventDetail(BaseModel):
    action: str
    outcome: AuditOutcome
    error_code: str | None = None
    data_refs: EventDataRefs = Field(default_factory=EventDataRefs)
    detail: dict[str, Any] = Field(default_factory=dict)

    @field_validator("error_code")
    @classmethod
    def error_code_required_for_failure(cls, v, info):
        outcome = info.data.get("outcome")
        if outcome in (AuditOutcome.fail, AuditOutcome.deny) and not v:
            return "UNKNOWN_ERROR"
        return v


# ---- Top-level audit event ----


class AuditEvent(BaseModel):
    # Sequence + identity
    audit_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    sequence: int

    # subject
    subject_nhs_number: str

    # Timing
    event_time: datetime

    # System identity
    # service_name: str
    organisation: str

    # Correlation
    request_id: str | None = None
    trace_id: str | None = None

    # SAML attributes
    saml: SAMLAttributes

    # Event
    event: AuditEventDetail

    # Device (SHOULD)
    device: DeviceInfo | None = None

    # user id from saml
    @computed_field  # type: ignore[misc]
    @property
    def user_id(self) -> str | None:
        return self.saml.subject_id

    @computed_field  # type: ignore[misc]
    @property
    def role_profile(self) -> dict:
        return self.saml.role.model_dump(by_alias=True) if self.saml.role else {}

    @computed_field  # type: ignore[misc]
    @property
    def purpose_of_use(self) -> dict:
        return self.saml.purpose_of_use.model_dump(by_alias=True) if self.saml.purpose_of_use else {}

    @computed_field  # type: ignore[misc]
    @property
    def subject_ref(self) -> str | None:
        """
        Pseudonymous patient reference derived from NHS number using AUDIT_SUBJECT_SECRET.
        Returns None if secret or nhs number not available.
        """
        nhsno = self.subject_nhs_number
        secret = os.getenv("API_KEY")
        if not nhsno or not secret:
            return None
        return _subject_ref_from_nhs_number(nhsno, secret)

    # Safety: forbid unknown fields
    model_config = {"extra": "forbid"}
