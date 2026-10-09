import logging
import os

logger = logging.getLogger(__name__)


class InvalidSAMLContext(ValueError):
    pass


def extract_trusted_saml_assertion(envelope: dict) -> dict:
    header = envelope.get("Header") or {}
    security = header.get("Security") or {}
    assertions = security.get("Assertion")

    if not assertions:
        logger.warning("SAML Validation Failed: Missing SAML assertion")
        raise InvalidSAMLContext("Missing SAML assertion")

    if isinstance(assertions, list):
        if len(assertions) != 1:
            logger.warning("SAML Validation Failed: Expected exactly one SAML assertion")
            raise InvalidSAMLContext("Expected exactly one SAML assertion")
        assertion = assertions[0]
    elif isinstance(assertions, dict):
        assertion = assertions
    else:
        logger.warning("SAML Validation Failed: Invalid SAML assertion structure")
        raise InvalidSAMLContext("Invalid SAML assertion structure")

    raw_issuer = assertion.get("Issuer")
    if isinstance(raw_issuer, dict):
        raw_issuer = raw_issuer.get("#text")

    if not isinstance(raw_issuer, str) or not raw_issuer.strip():
        logger.warning("SAML Validation Failed: Missing SAML issuer")
        raise InvalidSAMLContext("Missing SAML issuer")

    issuer = raw_issuer.strip()

    trusted = {
        item.strip()
        for item in os.environ.get("SAML_TRUSTED_ISSUER", "urn:nhs:names:services:spine").split("|")
        if item.strip()
    }

    if not trusted or issuer not in trusted:
        logger.warning("SAML Validation Failed: Untrusted SAML issuer")
        raise InvalidSAMLContext("Untrusted SAML issuer")

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
        raise InvalidSAMLContext("Incomplete SAML security context")
