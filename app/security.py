"""
Security Module

This module handles JWT (JSON Web Token) creation and management for NHS API authentication.
It implements the NHS Digital specifications for JWT creation as documented in:
https://digital.nhs.uk/developer/guides-and-documentation/security-and-authorisation

The module provides two main JWT creation functions:
1. pds_jwt: Creates JWTs for PDS FHIR API authentication
2. create_jwt: Creates JWTs for GP Connect access

All tokens are signed using RS512 algorithm and have a 5-minute expiration time.
"""

import os
import uuid
from time import time

import jwt

JWTKEY = os.getenv("JWTKEY")


def pds_jwt(issuer: str, subject: str, audience: str, key_id: str) -> str:
    """
    Creates a JWT for PDS FHIR API authentication.

    Args:
        issuer (str): The JWT issuer (iss claim)
        subject (str): The JWT subject (sub claim)
        audience (str): The intended audience (aud claim)
        key_id (str): The key identifier (kid header)

    Returns:
        str: Encoded JWT string

    Note:
        The token is signed using RS512 algorithm and includes:
        - A unique JWT ID (jti claim)
        - 5-minute expiration time (exp claim)
    """
    headers = {"typ": "JWT", "kid": key_id}
    payload = {
        "sub": subject,
        "iss": issuer,
        "jti": str(uuid.uuid4()),
        "aud": audience,
        "exp": int(time()) + 300,
    }

    # Get private key from environment or file
    if JWTKEY is not None:
        private_key = JWTKEY
    else:
        with open("keys/test-1.pem", "r") as f:
            private_key = f.read()

    return jwt.encode(payload, key=private_key, algorithm="RS512", headers=headers)


def create_jwt(
    audit: dict,
    audience: str = "https://orange.testlab.nhs.uk/B82617/STU3/1/gpconnect/documents/fhir",
) -> str:
    """
    Creates a JWT for GP Connect access with specific claims required by NHS Digital.

    Args:
        audit (dict): Audit information for the JWT from teh SOAP SAML headers
        audience (str): The intended audience (aud claim). Defaults to test environment.

    Returns:
        str: Encoded JWT string

    Note:
        This function creates a JWT with specific claims required for GP Connect:
        - reason_for_request
        - requested_scope
        - requesting_device
        - requesting_organization
        - requesting_practitioner

    TODO:
        - Make requesting device dynamic
        - Make requesting organisation dynamic
        - Make requesting practitioner dynamic
        - Make audience dynamic
    """
    created_time = int(time())
    payload = {
        "iss": "https://orange.testlab.nhs.uk/",
        "sub": "1",
        "aud": audience,
        "iat": created_time,
        "exp": created_time + 300,
        "reason_for_request": "directcare",
        "requested_scope": "patient/*.read",
        "requesting_device": {
            "resourceType": "Device",
            "identifier": [
                {
                    "system": "https://orange.testlab.nhs.uk/gpconnect-demonstrator/Id/local-system-instance-id",
                    "value": "gpcdemonstrator-1-orange",
                }
            ],
            "model": "GP Connect Demonstrator",
            "version": "1.5.0",
        },
        "requesting_organization": {
            "resourceType": "Organization",
            "identifier": [
                {
                    "system": "https://fhir.nhs.uk/Id/ods-organization-code",
                    "value": "A11111",
                }
            ],
            "name": "Consumer organisation name",
        },
        "requesting_practitioner": {
            "resourceType": "Practitioner",
            "id": "1",
            "identifier": [
                {
                    "system": "https://fhir.nhs.uk/Id/sds-user-id",
                    "value": "111111111111",
                },
                {
                    "system": "https://fhir.nhs.uk/Id/sds-role-profile-id",
                    "value": "22222222222222",
                },
                {
                    "system": "https://orange.testlab.nhs.uk/gpconnect-demonstrator/Id/local-user-id",
                    "value": "1",
                },
            ],
            "name": [
                {"family": "Demonstrator", "given": ["GPConnect"], "prefix": ["Dr"]}
            ],
        },
    }
    return jwt.encode(payload, headers={"alg": "none", "typ": "JWT"}, key=None)


if __name__ == "__main__":
    # Example usage
    token = create_jwt()
    print(token)
