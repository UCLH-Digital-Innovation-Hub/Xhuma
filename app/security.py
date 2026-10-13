"""
Security Module

This module handles JWT (JSON Web Token) creation and management for NHS API authentication.
It implements the NHS Digital specifications for JWT creation as documented in:
https://digital.nhs.uk/developer/guides-and-documentation/security-and-authorisation

The module provides two main JWT creation functions:
1. pds_jwt: Creates JWTs for PDS FHIR API authentication
2. create_jwt: Creates JWTs for GP Connect access

PDS tokens are signed using RS512; GP Connect tokens use the none algorithm.
Both have a 5-minute expiration time.
"""

import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import jwt
from azure.identity import ManagedIdentityCredential
from azure.keyvault.secrets import SecretClient
from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader
from jwcrypto import jwk

from .audit.models import SAMLAttributes

logger = logging.getLogger(__name__)

API_KEY = os.getenv("API_KEY")
api_key_header_scheme = APIKeyHeader(name="X-API-Key", auto_error=True)


async def verify_api_key(api_key_header: str = Security(api_key_header_scheme)):
    expected_key = os.getenv("API_KEY")
    if not expected_key or not expected_key.strip():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server authentication misconfigured",
        )
    if api_key_header != expected_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API Key",
        )
    return api_key_header


def fix_pem_formatting(pem_string: str) -> str:
    if not pem_string:
        return pem_string
    pem_string = pem_string.replace("\\n", "\n")
    if "\n" not in pem_string:
        import re

        formatted_certs = []
        matches = re.finditer(r"(-----BEGIN [^-]+-----)(.*?)(-----END [^-]+-----)", pem_string)
        for match in matches:
            header = match.group(1)
            # Remove all whitespace from the base64 body
            raw_body = re.sub(r"\s+", "", match.group(2))
            # Chunk the body into 64-character lines
            body_lines = [raw_body[i : i + 64] for i in range(0, len(raw_body), 64)]
            body = "\n".join(body_lines)
            footer = match.group(3)
            formatted_certs.append(f"{header}\n{body}\n{footer}")

        if formatted_certs:
            return "\n".join(formatted_certs)
    return pem_string


def _clean_public_jwk(jwk_dict: dict) -> dict:
    """Ensure no private key parameters or internal fields are exposed in public JWK."""
    for priv_param in ("d", "p", "q", "dp", "dq", "qi", "dmp1", "dmq1", "coeff", "oth", "k"):
        jwk_dict.pop(priv_param, None)
    return jwk_dict


class _JWKSCache:
    def __init__(self):
        self.public_keys: list[dict] | None = None
        self.cached_at: float = 0.0

    def clear(self):
        self.public_keys = None
        self.cached_at = 0.0


_jwks_cache = _JWKSCache()


def clear_jwks_cache():
    """Clear the in-memory public JWKS cache (for testing or instance reset)."""
    _jwks_cache.clear()


def get_key_vault_publishable_jwks() -> list[dict]:
    """
    Retrieve at most the newest two unique publishable public JWKs from Azure Key Vault.

    A version is publishable if:
    - enabled is True
    - not expired (expires_on is None or > now)
    Future not_before versions are publishable so clients can fetch keys ahead of activation.
    Versions are evaluated newest-first and deduplicated by deterministic kid. Older versions
    are inspected until up to two unique JWKs have been collected or eligible versions are exhausted.
    """
    vault_url = os.getenv("KEY_VAULT_URL")
    if not vault_url:
        return []

    secret_name = os.getenv("JWTKEY_SECRET_NAME", "jwtkey")
    with ManagedIdentityCredential() as credential:
        with SecretClient(vault_url=vault_url, credential=credential) as client:
            now = datetime.now(timezone.utc)
            publishable_props = []
            for prop in client.list_properties_of_secret_versions(secret_name):
                if not prop.enabled:
                    continue
                if prop.expires_on:
                    exp = prop.expires_on if prop.expires_on.tzinfo else prop.expires_on.replace(tzinfo=timezone.utc)
                    if exp <= now:
                        continue
                publishable_props.append(prop)

            def _sort_key(p):
                created = p.created_on
                if created is None:
                    created = datetime.min.replace(tzinfo=timezone.utc)
                elif not created.tzinfo:
                    created = created.replace(tzinfo=timezone.utc)
                return (created, p.version or "")

            publishable_props.sort(key=_sort_key, reverse=True)
            if not publishable_props:
                raise RuntimeError(f"No enabled active versions of secret '{secret_name}' found in Key Vault")

            keys = []
            seen_kids = set()
            for prop in publishable_props:
                secret = client.get_secret(secret_name, version=prop.version)
                val = getattr(secret, "value", None)
                if not val or not val.strip():
                    continue
                pem_str = fix_pem_formatting(val)
                jwk_obj = jwk.JWK.from_pem(pem_str.encode("utf-8"))
                pub_jwk = jwk_obj.export_public(as_dict=True)
                pub_jwk["alg"] = "RS512"
                pub_jwk["use"] = "sig"
                _clean_public_jwk(pub_jwk)
                kid = pub_jwk.get("kid") or jwk_obj.thumbprint()
                if kid in seen_kids:
                    continue
                pub_jwk["kid"] = kid
                seen_kids.add(kid)
                keys.append(pub_jwk)
                if len(keys) == 2:
                    break

            if not keys:
                raise RuntimeError(f"No valid public keys loaded from secret '{secret_name}' in Key Vault")
            return keys


def get_key_vault_signable_key() -> tuple[str, str]:
    """
    Retrieve the current signable private key (PEM, kid) from Azure Key Vault.

    A version is signable if:
    - enabled is True
    - not expired (expires_on is None or > now)
    - active (not_before is None or <= now)

    Always selects the newest signable version. Fails closed if none available.
    """
    vault_url = os.getenv("KEY_VAULT_URL")
    if not vault_url:
        raise RuntimeError("KEY_VAULT_URL not configured")

    secret_name = os.getenv("JWTKEY_SECRET_NAME", "jwtkey")
    with ManagedIdentityCredential() as credential:
        with SecretClient(vault_url=vault_url, credential=credential) as client:
            now = datetime.now(timezone.utc)
            signable_props = []
            for prop in client.list_properties_of_secret_versions(secret_name):
                if not prop.enabled:
                    continue
                if prop.expires_on:
                    exp = prop.expires_on if prop.expires_on.tzinfo else prop.expires_on.replace(tzinfo=timezone.utc)
                    if exp <= now:
                        continue
                if prop.not_before:
                    nb = prop.not_before if prop.not_before.tzinfo else prop.not_before.replace(tzinfo=timezone.utc)
                    if nb > now:
                        continue
                signable_props.append(prop)

            def _sort_key(p):
                created = p.created_on
                if created is None:
                    created = datetime.min.replace(tzinfo=timezone.utc)
                elif not created.tzinfo:
                    created = created.replace(tzinfo=timezone.utc)
                return (created, p.version or "")

            signable_props.sort(key=_sort_key, reverse=True)
            if not signable_props:
                raise RuntimeError(f"No active signable versions of secret '{secret_name}' found in Key Vault")

            target_prop = signable_props[0]
            secret = client.get_secret(secret_name, version=target_prop.version)
            val = getattr(secret, "value", None)
            if not val or not val.strip():
                raise RuntimeError(f"Secret '{secret_name}' version {target_prop.version} has no value")

            pem_str = fix_pem_formatting(val)
            jwk_obj = jwk.JWK.from_pem(pem_str.encode("utf-8"))
            kid = jwk_obj.export_public(as_dict=True).get("kid") or jwk_obj.thumbprint()
            return pem_str, kid


def get_local_key() -> tuple[str, str, dict] | None:
    """Return (pem_str, kid, public_jwk) for local environment."""
    jwt_key = os.getenv("JWTKEY")
    if jwt_key and jwt_key.strip():
        pem_str = fix_pem_formatting(jwt_key)
        jwk_obj = jwk.JWK.from_pem(pem_str.encode("utf-8"))
        pub_jwk = jwk_obj.export_public(as_dict=True)
        pub_jwk["alg"] = "RS512"
        pub_jwk["use"] = "sig"
        _clean_public_jwk(pub_jwk)
        kid = pub_jwk.get("kid") or jwk_obj.thumbprint()
        pub_jwk["kid"] = kid
        return pem_str, kid, pub_jwk

    key_path = Path("keys/test-1.pem")
    if os.getenv("ENV", "prod").lower() in ("dev", "local") and key_path.is_file():
        pem_str = fix_pem_formatting(key_path.read_text())
        jwk_obj = jwk.JWK.from_pem(pem_str.encode("utf-8"))
        pub_jwk = jwk_obj.export_public(as_dict=True)
        pub_jwk["alg"] = "RS512"
        pub_jwk["use"] = "sig"
        _clean_public_jwk(pub_jwk)
        kid = pub_jwk.get("kid") or jwk_obj.thumbprint()
        pub_jwk["kid"] = kid
        return pem_str, kid, pub_jwk

    return None


def get_jwk_keyset() -> list[dict]:
    """
    Return the list of public JWKs for /jwk (current + previous when rotating).
    Utilises a bounded in-memory cache with a TTL (default 60s) for public keys.
    Never caches private key material.
    If Key Vault refresh fails and a last-known-good keyset exists, serves it temporarily.
    If no keys have ever been obtained, raises an exception (leading to HTTP 503).
    """
    ttl = int(os.getenv("JWKS_CACHE_TTL_SECONDS", "60"))
    now = time.monotonic()

    # If cache is valid and not expired, return cached public keys
    if _jwks_cache.public_keys is not None and (now - _jwks_cache.cached_at) < ttl:
        return _jwks_cache.public_keys

    # Attempt refresh from authoritative source
    try:
        if os.getenv("KEY_VAULT_URL"):
            keys = get_key_vault_publishable_jwks()
        else:
            local = get_local_key()
            keys = [local[2]] if local else []

        if keys:
            _jwks_cache.public_keys = keys
            _jwks_cache.cached_at = now
            return keys
        else:
            if _jwks_cache.public_keys is not None:
                logger.warning("No JWK keys returned; serving last-known-good public JWKS")
                return _jwks_cache.public_keys
            raise RuntimeError("No JWK keys configured")
    except Exception as e:
        logger.warning("Failed to refresh public JWKS: %s", e)
        if _jwks_cache.public_keys is not None:
            logger.info("Serving last-known-good public JWKS after refresh failure")
            return _jwks_cache.public_keys
        raise


def get_signing_key() -> tuple[str, str]:
    """
    Return (private_pem, kid) for the current signable active key.
    Always signs with the newest signable key.
    Fails closed if Key Vault is unavailable or no signable key exists.
    """
    if os.getenv("KEY_VAULT_URL"):
        return get_key_vault_signable_key()

    local = get_local_key()
    if local:
        return local[0], local[1]

    raise FileNotFoundError("No JWT signing key configured; set KEY_VAULT_URL or a local JWTKEY/test PEM.")


def pds_jwt(issuer: str, subject: str, audience: str) -> str:
    """
    Creates a JWT for PDS FHIR API authentication.

    Args:
        issuer (str): The JWT issuer (iss claim)
        subject (str): The JWT subject (sub claim)
        audience (str): The intended audience (aud claim)

    Returns:
        str: Encoded JWT string

    Note:
        The token is signed using RS512 algorithm and includes:
        - A unique JWT ID (jti claim)
        - 5-minute expiration time (exp claim)
        - A key ID derived from the newest signable key
    """
    payload = {
        "sub": subject,
        "iss": issuer,
        "jti": str(uuid.uuid4()),
        "aud": audience,
        "exp": int(time.time()) + 300,
    }

    private_key, key_id = get_signing_key()
    headers = {"alg": "RS512", "typ": "JWT", "kid": key_id}

    return jwt.encode(payload, private_key, algorithm="RS512", headers=headers)


def create_jwt(
    audit: SAMLAttributes,
    audience: str = "https://orange.testlab.nhs.uk/B82617/STU3/1/gpconnect/documents/fhir",
    *,
    issuer: str,
) -> str:
    """
    Creates a JWT for GP Connect access with specific claims required by NHS Digital.

    Args:
        audit (dict): Audit information for the JWT from the SOAP SAML headers
        audience (str): The intended audience (aud claim). Defaults to test environment.
        issuer (str): The consumer's Spine Endpoint address resolved through SDS.

    Returns:
        str: Encoded JWT string

    Note:
        This function creates a JWT with specific claims required for GP Connect:
        - reason_for_request
        - requested_scope
        - requesting_device
        - requesting_organization
        - requesting_practitioner

    """
    if not issuer or not issuer.strip():
        raise ValueError("A consumer Spine Endpoint address is required as the GP Connect JWT issuer")
    created_time = int(time.time())

    subject_id_str = audit.subject_id
    try:
        family, given = subject_id_str.split(", ", 1)
    except ValueError:
        family, given = subject_id_str, "User"

    payload = {
        "iss": issuer,
        "sub": subject_id_str,
        "aud": audience,
        "iat": created_time,
        "exp": created_time + 300,
        "reason_for_request": "directcare",
        "requested_scope": "patient/*.read",
        "requesting_device": {
            "resourceType": "Device",
            "identifier": [
                {
                    "system": "https://xhuma.co.uk",
                    "value": os.getenv("ORG_ASID", "1"),
                }
            ],
            "model": "Xhuma",
            "version": os.getenv("VERSION", "0.9"),
        },
        "requesting_organization": {
            "resourceType": "Organization",
            "identifier": [
                {
                    "system": "https://fhir.nhs.uk/Id/ods-organization-code",
                    "value": os.getenv("ORG_CODE", "RRV00"),
                },
                {
                    "system": "2.16.840.1.113883.2.1.4",
                    "value": audit.organization_id,
                },
            ],
            "name": audit.organization,
        },
        "requesting_practitioner": {
            "resourceType": "Practitioner",
            "id": subject_id_str,
            "identifier": [
                {
                    "system": "https://fhir.nhs.uk/Id/sds-user-id",
                    "value": "UNK",  # As per NHS spec when not using NHS smartcard
                },
                {
                    "system": "https://fhir.nhs.uk/Id/sds-role-profile-id",
                    "value": "UNK",  # As per NHS spec when not using NHS smartcard
                },
                {
                    "system": audit.organization,
                    "value": subject_id_str,
                },
            ],
            "name": [
                {
                    "family": family,
                    "given": [given],
                }
            ],
        },
    }

    # audit_role = audit.role.model_dump(by_alias=True)
    # audit_role.pop("@xsi:type", None)  # Remove XML type info for JWT payload
    # audit_role.pop("nullFlavor", None)  # Remove null flavor if present

    audit_role = {
        "system": audit.role.codeSystemName if audit.role else "",
        "value": audit.role.code if audit.role else "",
    }

    # print("Adding role to JWT payload:", audit_role)

    payload["requesting_practitioner"]["identifier"].append(audit_role)

    # print("JWT PAYLOAD")
    # print(payload)
    # write payload to file for debugging
    # with open("app/logs/int_troubleshooting/jwt_payload.json", "w") as f:
    #     import json

    #     json.dump(payload, f, indent=4)
    return jwt.encode(payload, key=None, algorithm="none", headers={"alg": "none", "typ": "JWT"})


if __name__ == "__main__":
    import asyncio

    from app.pds.pds import lookup_self_issuer

    # Example usage
    audit = {
        "subject_id": "CONE, Stephen",
        "organization": "UCLH - University College London Hospitals - TST",
        "organization_id": "urn:oid:1.2.840.114350.1.13.525.3.7.3.688884.100",
        "home_community_id": "urn:oid:1.2.840.114350.1.13.525.3.7.3.688884.100",
        "role": {
            "Role": {
                "@xsi:type": "CE",
                "@code": "224608005",
                "@codeSystem": "2.16.840.1.113883.6.96",
                "@codeSystemName": "SNOMED_CT",
                "@displayName": "Administrative healthcare staff",
                "@xmlns": "urn:hl7-org:v3",
                "@xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
                "@xmlns:xsd": "http://www.w3.org/2001/XMLSchema",
            }
        },
        "purpose_of_use": {
            "PurposeForUse": {
                "@xsi:type": "CE",
                "@code": "TREATMENT",
                "@codeSystem": "2.16.840.1.113883.3.18.7.1",
                "@codeSystemName": "nhin-purpose",
                "@displayName": "Treatment",
                "@xmlns": "urn:hl7-org:v3",
                "@xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
                "@xmlns:xsd": "http://www.w3.org/2001/XMLSchema",
            }
        },
        "resource_id": "9690937278^^^&2.16.840.1.113883.2.1.4.1&ISO",
    }
    print("RAW TOKEN")
    token = create_jwt(SAMLAttributes.model_validate(audit), issuer=asyncio.run(lookup_self_issuer()))
    print(token)
