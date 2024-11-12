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
from typing import Dict, Any, Optional

import jwt
from prometheus_client import Counter, Histogram
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from .config import get_logger, SECURITY_EVENTS
from .handlers import correlation_id_ctx_var

# Initialize logger
logger = get_logger("security")

# Initialize tracer
tracer = trace.get_tracer(__name__)

# Initialize metrics
TOKEN_OPERATIONS = Counter(
    "token_operations_total",
    "Total count of token operations",
    ["operation", "status"]
)

TOKEN_CREATION_DURATION = Histogram(
    "token_creation_duration_seconds",
    "Token creation duration in seconds",
    ["token_type"]
)

AUTH_EVENTS = Counter(
    "authentication_events_total",
    "Total count of authentication events",
    ["event_type", "status"]
)

JWTKEY = os.getenv("JWTKEY")

def mask_sensitive_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Mask sensitive data in dictionaries for logging.
    
    :param data: Dictionary containing potentially sensitive data
    :type data: Dict[str, Any]
    :return: Dictionary with sensitive data masked
    :rtype: Dict[str, Any]
    """
    sensitive_fields = {
        "identifier", "value", "given", "family", "prefix",
        "sub", "requesting_practitioner", "requesting_organization"
    }
    
    masked = {}
    for key, value in data.items():
        if isinstance(value, dict):
            masked[key] = mask_sensitive_data(value)
        elif isinstance(value, list):
            masked[key] = [
                mask_sensitive_data(item) if isinstance(item, dict) else item 
                for item in value
            ]
        elif key in sensitive_fields:
            masked[key] = "***REDACTED***"
        else:
            masked[key] = value
    return masked

def log_security_event(event_type: str, details: Dict[str, Any], 
                      success: bool = True, error: Optional[Exception] = None) -> None:
    """
    Log a security event with appropriate severity and metrics.
    
    :param event_type: Type of security event from SECURITY_EVENTS
    :type event_type: str
    :param details: Event details to log
    :type details: Dict[str, Any]
    :param success: Whether the event was successful
    :type success: bool
    :param error: Exception if an error occurred
    :type error: Optional[Exception]
    """
    correlation_id = correlation_id_ctx_var.get(None)
    status = "success" if success else "failure"
    
    # Track metric
    AUTH_EVENTS.labels(
        event_type=event_type,
        status=status
    ).inc()
    
    # Mask sensitive data
    safe_details = mask_sensitive_data(details)
    
    # Add common fields
    log_data = {
        "event_type": event_type,
        "correlation_id": correlation_id,
        "status": status,
        **safe_details
    }
    
    if success:
        logger.info(
            f"Security event: {SECURITY_EVENTS.get(event_type, event_type)}",
            extra=log_data
        )
    else:
        logger.error(
            f"Security event failed: {SECURITY_EVENTS.get(event_type, event_type)}",
            extra={**log_data, "error": str(error) if error else None}
        )

def pds_jwt(issuer: str, subject: str, audience: str, key_id: str) -> str:
    """
    Creates a JWT for PDS FHIR API authentication.
    
    :param issuer: The JWT issuer (iss claim)
    :type issuer: str
    :param subject: The JWT subject (sub claim)
    :type subject: str
    :param audience: The intended audience (aud claim)
    :type audience: str
    :param key_id: The key identifier (kid header)
    :type key_id: str
    :return: Encoded JWT string
    :rtype: str
    """
    with tracer.start_as_current_span("create_pds_jwt") as span:
        try:
            with TOKEN_CREATION_DURATION.labels(token_type="pds").time():
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

                token = jwt.encode(payload, key=private_key, algorithm="RS512", headers=headers)
                
                # Log success
                log_security_event(
                    "TOKEN_CREATED",
                    {
                        "token_type": "pds",
                        "issuer": issuer,
                        "audience": audience,
                    }
                )
                
                TOKEN_OPERATIONS.labels(
                    operation="create",
                    status="success"
                ).inc()
                
                span.set_status(Status(StatusCode.OK))
                return token
                
        except Exception as e:
            # Log failure
            log_security_event(
                "TOKEN_CREATION_FAILED",
                {
                    "token_type": "pds",
                    "issuer": issuer,
                    "audience": audience,
                },
                success=False,
                error=e
            )
            
            TOKEN_OPERATIONS.labels(
                operation="create",
                status="failure"
            ).inc()
            
            span.set_status(Status(StatusCode.ERROR))
            span.record_exception(e)
            raise

def create_jwt(
    audience: str = "https://orange.testlab.nhs.uk/B82617/STU3/1/gpconnect/documents/fhir",
) -> str:
    """
    Creates a JWT for GP Connect access with specific claims required by NHS Digital.
    
    :param audience: The intended audience (aud claim). Defaults to test environment.
    :type audience: str
    :return: Encoded JWT string
    :rtype: str
    """
    with tracer.start_as_current_span("create_gpconnect_jwt") as span:
        try:
            with TOKEN_CREATION_DURATION.labels(token_type="gpconnect").time():
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
                
                token = jwt.encode(payload, headers={"alg": "none", "typ": "JWT"}, key=None)
                
                # Log success
                log_security_event(
                    "TOKEN_CREATED",
                    {
                        "token_type": "gpconnect",
                        "audience": audience,
                    }
                )
                
                TOKEN_OPERATIONS.labels(
                    operation="create",
                    status="success"
                ).inc()
                
                span.set_status(Status(StatusCode.OK))
                return token
                
        except Exception as e:
            # Log failure
            log_security_event(
                "TOKEN_CREATION_FAILED",
                {
                    "token_type": "gpconnect",
                    "audience": audience,
                },
                success=False,
                error=e
            )
            
            TOKEN_OPERATIONS.labels(
                operation="create",
                status="failure"
            ).inc()
            
            span.set_status(Status(StatusCode.ERROR))
            span.record_exception(e)
            raise

if __name__ == "__main__":
    # Example usage
    token = create_jwt()
    print(token)
