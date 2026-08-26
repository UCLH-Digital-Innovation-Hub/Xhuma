import base64
import hashlib
import hmac
import os
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, computed_field, field_validator

from ..ccda.models.datatypes import CD


def _subject_ref_from_nhs_number(
    nhs_number: str, secret: str, *, version: str = "v1"
) -> str:
    """
    HMAC-based, non-reversible pseudonym. Safe to store in logs/audit DB.
    Args:
        nhs_number (str): NHS number to store
        secret (str): Secret key for HMAC
        version (str, optional): Version string for future-proofing. Defaults to "v1".
    Returns:
        str: Pseudonym string for audit storage
    """
    mac = hmac.new(
        secret.encode("utf-8"), nhs_number.encode("utf-8"), hashlib.sha256
    ).digest()
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
    subject_id: Optional[str] = None
    organization: Optional[str] = None
    organization_id: Optional[str] = None
    home_community_id: Optional[str] = None

    role: Optional[CD] = None
    purpose_of_use: Optional[CD] = None

    # XACML resource-id (contains patient identifier)
    resource_id: Optional[str] = None

    model_config = {"extra": "forbid"}


class OrganisationRef(BaseModel):
    name: Optional[str] = None
    id: Optional[str] = None
    home_community_id: Optional[str] = None


class UserIdentity(BaseModel):
    user_id: Optional[str] = None
    name: Optional[str] = None
    role_profile: Optional[CD] = None
    organisation: Optional[OrganisationRef] = None
    urp_id: Optional[str] = None
    purpose_of_use: Optional[Dict[str, Any]] = None  # keep structured


class AuthorityIdentity(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = None


class DeviceInfo(BaseModel):
    ip: Optional[str] = None
    user_agent: Optional[str] = None
    host: Optional[str] = None


class EventDataRefs(BaseModel):
    # subject_ref: Optional[str]
    message_id: Optional[str] = None
    document_id: Optional[str] = None


class AuditEventDetail(BaseModel):
    action: str
    outcome: AuditOutcome
    error_code: Optional[str] = None
    data_refs: EventDataRefs = Field(default_factory=EventDataRefs)
    detail: Dict[str, Any] = Field(default_factory=dict)

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
    request_id: Optional[str] = None
    trace_id: Optional[str] = None

    # SAML attributes
    saml: SAMLAttributes

    # Event
    event: AuditEventDetail

    # Device (SHOULD)
    device: Optional[DeviceInfo] = None

    # user id from saml
    @computed_field  # type: ignore[misc]
    @property
    def user_id(self) -> Optional[str]:
        return self.saml.subject_id

    @computed_field  # type: ignore[misc]
    @property
    def role_profile(self) -> dict:
        return self.saml.role.model_dump(by_alias=True) if self.saml.role else {}

    @computed_field  # type: ignore[misc]
    @property
    def purpose_of_use(self) -> dict:
        return (
            self.saml.purpose_of_use.model_dump(by_alias=True)
            if self.saml.purpose_of_use
            else {}
        )

    @computed_field  # type: ignore[misc]
    @property
    def subject_ref(self) -> Optional[str]:
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
