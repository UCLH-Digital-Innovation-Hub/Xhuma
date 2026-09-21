from typing import Any

from app.audit.models import SAMLAttributes
from app.ccda.models.datatypes import CD  # adjust path


def process_saml_attributes(saml_header: dict) -> SAMLAttributes:
    """
    Process SAML attributes from SOAP header into a validated SAMLAttributes model.
    Role and PurposeOfUse are parsed as CD concept descriptors.
    """

    attr_map = {
        "urn:oasis:names:tc:xspa:1.0:subject:subject-id": "subject_id",
        "urn:oasis:names:tc:xspa:1.0:subject:organization": "organization",
        "urn:oasis:names:tc:xspa:1.0:subject:organization-id": "organization_id",
        "urn:nhin:names:saml:homeCommunityId": "home_community_id",
        "urn:oasis:names:tc:xacml:2.0:subject:role": "role",
        "urn:oasis:names:tc:xspa:1.0:subject:purposeofuse": "purpose_of_use",
        "urn:oasis:names:tc:xacml:2.0:resource:resource-id": "resource_id",
    }

    raw: dict[str, Any] = {}

    attributes = saml_header.get("Attribute", [])
    if isinstance(attributes, dict):
        attributes = [attributes]
    elif not isinstance(attributes, list):
        attributes = []

    for attribute in attributes:
        urn = attribute.get("@Name")
        if urn not in attr_map:
            continue

        key = attr_map[urn]
        value = attribute.get("AttributeValue")

        # Role and PurposeOfUse come wrapped, e.g. {"Role": {...}} / {"PurposeForUse": {...}}
        if key == "role" and isinstance(value, dict):
            cd_payload = value.get("Role") or value
            raw["role"] = CD.model_validate(cd_payload)

        elif key == "purpose_of_use" and isinstance(value, dict):
            cd_payload = value.get("PurposeForUse") or value
            raw["purpose_of_use"] = CD.model_validate(cd_payload)

        else:
            raw[key] = value

    return SAMLAttributes.model_validate(raw)


class AuditFailureException(Exception):
    """Raised when an audit event fails to process or persist to the database."""

    pass


async def attempt_audit(
    request: Any,
    *,
    nhs_number: str | None,
    saml: SAMLAttributes,
    action: str,
    outcome: Any,  # AuditOutcome
    error_code: str | None = None,
    detail: dict | None = None,
    message_id: str | None = None,
    document_id: str | None = None,
    request_id: str | None = None,
) -> None:
    """Attempt to write an audit event, failing the main request if it fails."""
    import logging
    from .build import build_audit_event
    from .store import insert_audit_event

    if not request or not hasattr(request, "app"):
        logging.error("AuditFailure: No request or app found")
        raise AuditFailureException("Audit context missing")

    SessionLocal = getattr(request.app.state, "SessionLocal", None)
    if not SessionLocal:
        logging.error("AuditFailure: No SessionLocal found in app state")
        raise AuditFailureException("Audit persistence context missing")

    try:
        async with SessionLocal() as session:
            ev = await build_audit_event(
                request=request,
                session=session,
                nhs_number=nhs_number,
                saml=saml,
                action=action,
                outcome=outcome,
                error_code=error_code,
                detail=detail,
                message_id=message_id,
                document_id=document_id,
                request_id=request_id,
            )
            await insert_audit_event(session, ev)
            await session.commit()
    except Exception:
        # Do not log raw database exceptions containing SQL parameters.
        logging.error("AuditFailure: Database persistence failed")
        raise AuditFailureException("Failed to persist audit event") from None
