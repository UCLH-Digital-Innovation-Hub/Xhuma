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

    """
    created_time = int(time())
    family, given = audit["subject_id"].split(", ")
    payload = {
        "iss": "https://orange.testlab.nhs.uk/",
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
            "version": os.getenv("VERSION", "1.0"),
        },
        "requesting_organization": {
            "resourceType": "Organization",
            "identifier": [
                {
                    "system": "https://fhir.nhs.uk/Id/ods-organization-code",
                    "value": os.getenv("ORG_CODE", "RRV"),
                }
            ],
            "name": audit["organization"],
        },
        # "requesting_practitioner": f"{audit["organisation_id"]}|{audit["subject_id"]}",
        "requesting_practitioner": {
            "resourceType": "Practitioner",
            "id": audit["subject_id"],
            "identifier": [
                {
                    "system": audit["organization_id"],
                    "value": audit["subject_id"],
                },
                {
                    # role ID
                    "system": audit["role"]["Role"]["@codeSystem"],
                    "value": audit["role"]["Role"]["@code"],
                },
            ],
            "name": humanname.HumanName(
                dict(
                    family=family,
                    given=[given],
                    prefix=["Dr"],
                )
            ).as_json(),
            # "name": [
            #     {"family": "Demonstrator", "given": ["GPConnect"], "prefix": ["Dr"]}
            # ],
        },
    }
    print("JWT PAYLOAD")
    print(payload)
    return jwt.encode(payload, headers={"alg": "none", "typ": "JWT"}, key=None)


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
