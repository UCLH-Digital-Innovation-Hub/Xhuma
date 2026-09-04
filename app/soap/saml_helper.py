import os


class InvalidSAMLContext(ValueError):
    pass


def extract_trusted_saml_assertion(envelope: dict) -> dict:
    header = envelope.get("Header") or {}
    security = header.get("Security") or {}
    assertions = security.get("Assertion")

    if not assertions:
        raise InvalidSAMLContext("Missing SAML assertion")

    if isinstance(assertions, list):
        if len(assertions) != 1:
            raise InvalidSAMLContext("Expected exactly one SAML assertion")
        assertion = assertions[0]
    elif isinstance(assertions, dict):
        assertion = assertions
    else:
        raise InvalidSAMLContext("Invalid SAML assertion structure")

    raw_issuer = assertion.get("Issuer")
    if isinstance(raw_issuer, dict):
        raw_issuer = raw_issuer.get("#text")

    if not isinstance(raw_issuer, str) or not raw_issuer.strip():
        raise InvalidSAMLContext("Missing SAML issuer")

    issuer = raw_issuer.strip()

    trusted = {
        item.strip()
        for item in os.environ.get(
            "SAML_TRUSTED_ISSUER", "urn:nhs:names:services:spine"
        ).split("|")
        if item.strip()
    }

    if not trusted or issuer not in trusted:
        raise InvalidSAMLContext("Untrusted SAML issuer")

    return assertion
