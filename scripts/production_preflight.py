#!/usr/bin/env python3
import os
import sys


def main():
    print("INFO: Running Production Preflight Checks...")

    errors = []
    warnings = []

    # 1. Expected ENV
    env = os.getenv("ENV", "").lower()
    if env != "prod":
        warnings.append(f"ENV is set to '{env}', expected 'prod'.")

    # 2. Required organisation config
    org_code = os.getenv("ORG_CODE")
    if not org_code:
        errors.append("ORG_CODE is missing.")

    org_asid = os.getenv("ORG_ASID")
    if not org_asid:
        errors.append("ORG_ASID is missing.")

    # 3. mTLS configuration
    for key in ["NHS_CLIENT_CERT", "NHS_CLIENT_KEY", "NHS_BUNDLE"]:
        if not os.getenv(key) and os.getenv("USE_RELAY") not in ("1", "true", "True"):
            errors.append(f"{key} is missing, required for non-relay direct mTLS mode.")

    # 4. SAML configuration
    saml_issuer = os.getenv("SAML_TRUSTED_ISSUER")
    if not saml_issuer:
        errors.append("SAML_TRUSTED_ISSUER is missing.")

    # 5. NHS_API_KEY distinction
    api_key = os.getenv("API_KEY")
    nhs_api_key = os.getenv("NHS_API_KEY")
    if not api_key:
        errors.append("API_KEY is missing.")
    if not nhs_api_key:
        errors.append("NHS_API_KEY is missing.")
    if api_key and nhs_api_key and api_key == nhs_api_key:
        if env == "prod":
            errors.append("API_KEY and NHS_API_KEY are identical. This is strictly forbidden in production.")
        else:
            warnings.append("API_KEY and NHS_API_KEY are identical. (Allowed in non-prod).")

    # 6. PDS Cache Fallback Warning
    if not os.getenv("PDS_CACHE_HMAC_SECRET"):
        warnings.append(
            "PDS_CACHE_HMAC_SECRET is missing. PDS cache falls back to API_KEY for HMAC. Consider migrating."
        )

    # 7. JWTKEY / RSA Policy
    jwt_key = os.getenv("JWTKEY")
    if not jwt_key:
        errors.append("JWTKEY is missing.")
    else:
        try:
            from cryptography.hazmat.backends import default_backend
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import rsa

            # Need to format PEM exactly like the app does for testing
            from app.security import fix_pem_formatting

            private_pem = fix_pem_formatting(jwt_key).encode("utf-8")

            private_key = serialization.load_pem_private_key(private_pem, password=None, backend=default_backend())
            if isinstance(private_key, rsa.RSAPrivateKey):
                if private_key.key_size != 4096:
                    errors.append(f"JWTKEY is {private_key.key_size}-bit RSA. Production policy requires 4096-bit RSA.")
            else:
                errors.append("JWTKEY is not an RSA key.")
        except Exception as e:
            errors.append(f"Failed to parse JWTKEY: {e}")

    # Report results
    print("\n--- Preflight Results ---")
    for w in warnings:
        print(f"WARNING: {w}")
    for e in errors:
        print(f"ERROR: {e}")

    if errors:
        print("\nFATAL: Preflight FAILED.")
        sys.exit(1)
    else:
        print("\nSUCCESS: Preflight PASSED.")
        sys.exit(0)


if __name__ == "__main__":
    main()
