"""
Security Module

This module handles JWT (JSON Web Token) creation and management for NHS API authentication.
It implements the NHS Digital specifications for JWT creation as documented in:
https://digital.nhs.uk/developer/guides-and-documentation/security-and-authorisation

The module provides two main JWT creation functions:
1. pds_jwt: Creates JWTs for PDS FHIR API authentication
2. create_jwt: Creates JWTs for GP Connect access

All tokens are signed using RS512 algorithm and have a 5-minute expiration time.

Additionally, this module provides JWK (JSON Web Key) functionality to expose
the public key in JWK format for JWT verification by relying parties.
"""

import os
import uuid
import base64
import json
from time import time
from typing import Dict, Any, Optional

from redis import Redis

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import JSONResponse

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.storage import RedisStorage

# Get JWT signing key from environment variable
JWTKEY = os.getenv("JWTKEY")

# Private key object will be lazily loaded
_private_key_obj = None

def validate_jwtkey():
    """
    Validate the JWTKEY environment variable.
    
    Raises:
        ValueError: If the JWTKEY is not set or not in a valid format
    """
    if not JWTKEY:
        raise ValueError("JWTKEY environment variable must be set for JWT signing")
    
    # Validate key format 
    if not JWTKEY.startswith("-----BEGIN PRIVATE KEY-----") and not JWTKEY.startswith("-----BEGIN RSA PRIVATE KEY-----"):
        raise ValueError("JWTKEY does not appear to be a valid PEM-formatted key. It should begin with '-----BEGIN PRIVATE KEY-----' or '-----BEGIN RSA PRIVATE KEY-----'")

def get_private_key():
    """
    Lazily load and return the private key object.
    
    Returns:
        RSAPrivateKey: The loaded private key object
        
    Raises:
        ValueError: If the key cannot be loaded or is not a valid RSA private key
    """
    global _private_key_obj
    
    if _private_key_obj is not None:
        return _private_key_obj
    
    # Validate the key first
    validate_jwtkey()
    
    try:
        key_obj = serialization.load_pem_private_key(
            JWTKEY.encode('utf-8'),
            password=None,
            backend=default_backend()
        )
        
        if not isinstance(key_obj, rsa.RSAPrivateKey):
            raise ValueError("The provided key is not an RSA private key")
            
        _private_key_obj = key_obj
        return _private_key_obj
    except Exception as e:
        # More specific error message based on the exception
        error_msg = str(e)
        if "Could not deserialize key data" in error_msg:
            raise ValueError(f"Invalid key format. Please ensure the JWTKEY contains a valid PEM-formatted RSA private key with proper line breaks. Error: {error_msg}")
        else:
            raise ValueError(f"Error loading RSA private key: {error_msg}")


def _int_to_base64url(value: int) -> str:
    """Convert an integer to a base64url-encoded string without padding."""
    value_bytes = value.to_bytes((value.bit_length() + 7) // 8, byteorder='big')
    encoded = base64.urlsafe_b64encode(value_bytes).decode('ascii')
    return encoded.rstrip('=')  # Remove any padding


def get_jwk(kid: str = "default-key-id") -> Dict[str, Any]:
    """
    Generate a JWK (JSON Web Key) from the private key.

    Args:
        kid (str): Key ID to include in the JWK

    Returns:
        Dict[str, Any]: JWK representation of the public key
    """
    try:
        # Lazily load the private key
        private_key = get_private_key()
        
        # Get the public key from the private key
        public_key = private_key.public_key()
        
        # Get the public numbers
        public_numbers = public_key.public_numbers()
        
        # Create JWK format
        jwk = {
            "kty": "RSA",
            "use": "sig",
            "alg": "RS512",
            "kid": kid,
            "n": _int_to_base64url(public_numbers.n),
            "e": _int_to_base64url(public_numbers.e),
        }
        
        return jwk
    except ValueError as e:
        # Return an error JWK for debugging
        return {
            "kty": "RSA",
            "use": "sig",
            "alg": "RS512",
            "kid": kid,
            "error": str(e)
        }


def get_jwks(kids: Optional[list] = None) -> Dict[str, Any]:
    """
    Generate a JWKS (JSON Web Key Set) containing the public key.
    
    Args:
        kids (Optional[list]): List of key IDs to include
        
    Returns:
        Dict[str, Any]: JWKS representation
    """
    if kids is None:
        kids = ["default-key-id"]
        
    keys = [get_jwk(kid) for kid in kids]
    return {"keys": keys}


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
    # Validate the key before attempting to use it
    validate_jwtkey()
    
    headers = {"alg": "RS512", "typ": "JWT", "kid": key_id}
    payload = {
        "sub": subject,
        "iss": issuer,
        "jti": str(uuid.uuid4()),
        "aud": audience,
        "exp": int(time()) + 300,
    }

    # Use the securely stored private key
    return jwt.encode(payload, key=JWTKEY, algorithm="RS512", headers=headers)


def create_jwt(
    audience: str = "https://orange.testlab.nhs.uk/B82617/STU3/1/gpconnect/documents/fhir",
    key_id: str = "default-key-id",
) -> str:
    """
    Creates a JWT for GP Connect access with specific claims required by NHS Digital.

    Args:
        audience (str): The intended audience (aud claim). Defaults to test environment.
        key_id (str): The key identifier (kid header). Defaults to "default-key-id".

    Returns:
        str: Encoded JWT string

    Note:
        This function creates a JWT with specific claims required for GP Connect:
        - reason_for_request
        - requested_scope
        - requesting_device
        - requesting_organization
        - requesting_practitioner

    The token is signed using RS512 algorithm and has a 5-minute expiration time.
    """
    # Validate the key before attempting to use it
    validate_jwtkey()
    
    created_time = int(time())
    headers = {"alg": "RS512", "typ": "JWT", "kid": key_id}
    payload = {
        "iss": "https://orange.testlab.nhs.uk/",
        "sub": "1",
        "aud": audience,
        "iat": created_time,
        "exp": created_time + 300,
        "jti": str(uuid.uuid4()),  # Added unique JWT ID
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
    return jwt.encode(payload, key=JWTKEY, algorithm="RS512", headers=headers)


# Add the JWK endpoint to the app.main module
def register_jwk_endpoint(app):
    """
    Register the JWK endpoint in the FastAPI app.
    This should be called from the main app module.
    
    Args:
        app: The FastAPI app instance
    """
    def real_ip(request: Request) -> str:
        """Resolve the client's real IP considering X-Forwarded-For."""
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host

    redis_url = os.getenv("SLOWAPI_REDIS_URL")
    if redis_url:
        try:
            storage = RedisStorage(Redis.from_url(redis_url))
            limiter = Limiter(key_func=real_ip, storage=storage)
        except Exception:
            limiter = Limiter(key_func=real_ip)
    else:
        limiter = Limiter(key_func=real_ip)

    app.state.limiter = limiter

    @app.exception_handler(RateLimitExceeded)
    async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
        return JSONResponse(status_code=429, content={"error": "Too many requests"})

    app.add_middleware(SlowAPIMiddleware)

    @app.get("/jwk")
    @limiter.limit("5/minute")
    async def get_jwk_endpoint(request: Request):
        """Endpoint to retrieve the JWK for token verification."""
        return get_jwk("default-key-id")

    @app.get("/jwks")
    @limiter.limit("10/minute")
    async def get_jwks_endpoint(request: Request):
        """Endpoint to retrieve the JWKS (multiple keys) for token verification."""
        return get_jwks()


if __name__ == "__main__":
    # Example usage
    token = create_jwt()
    print(token)
    
    # Print the JWK for verification
    print("\nJWK for verification:")
    print(json.dumps(get_jwk(), indent=2))
