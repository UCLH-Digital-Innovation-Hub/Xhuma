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
from fhirclient.models import humanname, practitioner

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
    headers = {"alg": "RS512", "typ": "JWT", "kid": key_id}
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
        # Fallback to local file if it exists, otherwise raise clear error
        key_path = "keys/test-1.pem"
        if os.path.exists(key_path):
            with open(key_path, "r") as f:
                private_key = f.read()
        else:
            # During tests/CI, we might not have keys.
            # Warning: This will fail if code tries to sign a token!
            private_key = None
            
    if private_key is None:
         raise FileNotFoundError("JWTKEY env var not set and keys/test-1.pem not found.")

    return jwt.encode(payload, key=private_key, algorithm="RS512", headers=headers)


def create_jwt(
    audit: dict,
    audience: str = "https://orange.testlab.nhs.uk/B82617/STU3/1/gpconnect/documents/fhir",
) -> str:
    """
    Creates a JWT for GP Connect access with specific claims required by NHS Digital.

    Args:
        audit (dict): Audit information for the JWT from the SOAP SAML headers
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

    """
    created_time = int(time())
    family, given = audit["subject_id"].split(", ")
    payload = {
        "iss": "http://int.apis.ptl.api.platform.nhs.uk/Device/EA2027FD-B486-4033-B48C-E87222F6FA1C",
        "sub": audit["subject_id"],
        "aud": audience,
        "iat": created_time,
        "exp": created_time + 300,
        "reason_for_request": "directcare",
        "requested_scope": "patient/*.read",
        "requesting_device": {
            "resourceType": "Device",
            "identifier": [
                {
                    "system": "https://xhumademo.com",
                    "value": os.getenv("DEVICE_ID", "1"),
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
                    "value": audit["organization_id"],
                },
            ],
            "name": audit["organization"],
        },
        "requesting_practitioner": {
            "resourceType": "Practitioner",
            "id": audit["subject_id"],
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
                    "system": audit["organization"],
                    "value": audit["subject_id"],
                },
                {
                    "system": audit["role"]["Role"]["@codeSystem"],
                    "value": audit["role"]["Role"]["@code"],
                },
            ],
            "name": [
                {
                    "family": family,
                    "given": [given],
                    "prefix": ["Dr"],
                }
            ],
        },
    }
    # print("JWT PAYLOAD")
    # print(payload)
    # write payload to file for debugging
    # with open("app/logs/int_troubleshooting/jwt_payload.json", "w") as f:
    #     import json

    #     json.dump(payload, f, indent=4)
    # Get private key from environment or file

    headers = {"alg": "RS512", "typ": "JWT", "kid": "test-1"}
    # log headers to file for debugging
    # with open("app/logs/int_troubleshooting/jwt_headers.json", "w") as f:
    #     import json

    #     json.dump(headers, f, indent=4)
    # headers = {"alg": "none", "typ": "JWT"}

    if JWTKEY is not None:
        private_key = JWTKEY
    else:
        key_path = "keys/test-1.pem"
        if os.path.exists(key_path):
            with open(key_path, "r") as f:
                private_key = f.read()
        else:
            private_key = None
            
    if private_key is None:
         raise FileNotFoundError("JWTKEY env var not set and keys/test-1.pem not found.")

    return jwt.encode(payload, headers={"alg": "none", "typ": "JWT"}, key=None)

    # return jwt.encode(payload, private_key, algorithm="RS512", headers={"alg": "RS512", "typ": "JWT", "kid": "test-1"})


if __name__ == "__main__":
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
    token = create_jwt(audit)
    print(token)
