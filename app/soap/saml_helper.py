import logging
import os

from pydantic import ValidationError

from ..audit.audit import process_saml_attributes
from .faults import SoapFault

logger = logging.getLogger(__name__)


class InvalidSAMLContext(ValueError):
    def __init__(self, message: str, fault_code: str = "InvalidSecurity"):
        super().__init__(message)
        self.fault_code = fault_code


def extract_trusted_saml_assertion(envelope: dict) -> dict:
    header = envelope.get("Header") or {}
    security = header.get("Security") or {}
    assertions = security.get("Assertion")

    if not assertions:
        logger.warning("SAML Validation Failed: Missing SAML assertion")
        print("SAML Validation Failed: Missing SAML assertion", flush=True)
        raise InvalidSAMLContext("Missing SAML assertion")

    if isinstance(assertions, list):
        if len(assertions) != 1:
            logger.warning("SAML Validation Failed: Expected exactly one SAML assertion")
            print("SAML Validation Failed: Expected exactly one SAML assertion", flush=True)
            raise InvalidSAMLContext("Expected exactly one SAML assertion")
        assertion = assertions[0]
    elif isinstance(assertions, dict):
        assertion = assertions
    else:
        logger.warning("SAML Validation Failed: Invalid SAML assertion structure")
        print("SAML Validation Failed: Invalid SAML assertion structure", flush=True)
        raise InvalidSAMLContext("Invalid SAML assertion structure")

    raw_issuer = assertion.get("Issuer")
    if isinstance(raw_issuer, dict):
        raw_issuer = raw_issuer.get("#text")

    if not isinstance(raw_issuer, str) or not raw_issuer.strip():
        logger.warning("SAML Validation Failed: Missing SAML issuer")
        print("SAML Validation Failed: Missing SAML issuer", flush=True)
        raise InvalidSAMLContext("Missing SAML issuer")

    issuer = raw_issuer.strip()

    trusted = {
        item.strip()
        for item in os.environ.get("SAML_TRUSTED_ISSUER", "urn:nhs:names:services:spine").split("|")
        if item.strip()
    }

    if not trusted or issuer not in trusted:
        logger.warning("SAML Validation Failed: Untrusted SAML issuer")
        safe_issuer = issuer.replace("\r", "").replace("\n", "")[:512]
        print(
            f"SAML Validation Failed: Untrusted SAML issuer received={safe_issuer!r}",
            flush=True,
        )
        raise InvalidSAMLContext("Untrusted SAML issuer", "FailedAuthentication")

    return assertion


def validate_saml_attributes(saml_attrs):
    """Validates the SAML attributes and logs safely which ones are missing."""
    missing = []
    if not saml_attrs.subject_id:
        missing.append("subject_id")
    if not saml_attrs.organization:
        missing.append("organization")
    if not saml_attrs.organization_id:
        missing.append("organization_id")
    if not saml_attrs.role:
        missing.append("role")

    if missing:
        missing_str = ", ".join(missing)
        logger.warning(f"SAML Validation Failed: Incomplete SAML security context. Missing fields: {missing_str}")
        print(f"SAML Validation Failed: Incomplete SAML security context. Missing fields: {missing_str}", flush=True)
        raise InvalidSAMLContext("Incomplete SAML security context", "FailedAuthentication")


def extract_saml_attributes(envelope: dict):
    """Extract and validate trusted SAML attributes, reporting SOAP security faults."""
    header = envelope.get("Header")
    value = header.get("MessageID") if isinstance(header, dict) else None
    message_id = value if isinstance(value, str) else None
    try:
        assertion = extract_trusted_saml_assertion(envelope)
        attributes = process_saml_attributes(assertion.get("AttributeStatement", {}))
        validate_saml_attributes(attributes)
        return attributes
    except InvalidSAMLContext as e:
        raise SoapFault(str(e), subcode=f"wsse:{e.fault_code}", message_id=message_id) from None
    except (ValidationError, AttributeError, TypeError):
        raise SoapFault(
            "Invalid SAML security context",
            subcode="wsse:InvalidSecurityToken",
            message_id=message_id,
        ) from None
