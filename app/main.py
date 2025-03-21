"""
Xhuma Main Application Module

This module serves as the entry point for the Xhuma middleware service, which facilitates
the conversion of GP Connect structured records into CCDA format. It initializes the FastAPI
application, sets up routers, and handles startup configuration including JWT key management.

The service implements a stateless architecture with Redis caching and supports IHE ITI
profiles for healthcare interoperability.
"""

import json
import os
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fhirclient.models import bundle
from jwcrypto import jwk

from .gpconnect import gpconnect
from .pds import pds
from .redis_connect import redis_client
from .soap import soap

# Initialize FastAPI application
app = FastAPI(
    title="Xhuma",
    description="A stateless middleware service for GP Connect to CCDA conversion",
    version="1.0.0",
)

# Include routers for different service components
app.include_router(soap.router)
app.include_router(pds.router)
# app.include_router(gpconnect.router)  # Currently disabled

# Generate or retrieve registry ID from environment
REGISTRY_ID = os.getenv("REGISTRY_ID", str(uuid4()))


@app.on_event("startup")
async def startup_event():
    """
    Startup event handler that initializes necessary configurations.

    This function:
    1. Sets the registry ID in Redis
    2. Checks for existing JWK (JSON Web Key)
    3. Generates a new JWK from private key if none exists
    """
    # Store registry ID in Redis with 24 hour expiry
    redis_client.setex("registry", 86400, str(REGISTRY_ID).encode())

    # Handle JWK generation/verification
    if not os.path.isfile("keys/jwk.json"):
        # Generate new JWK from private key
        with open("keys/test-1.pem", "rb") as pemfile:
            private_pem = pemfile.read()
            public_jwk = jwk.JWK.from_pem(data=private_pem)
            jwk_json = public_jwk.export_public(as_dict=True)

            # Save generated JWK
            with open("keys/jwk.json", "w") as f:
                json.dump(jwk_json, f)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def root():
    """
    Root endpoint that serves the welcome page with service information.

    Returns:
        HTMLResponse: A welcome page containing service description and endpoint examples.
    """
    return """
    <html>
        <head>
            <title>Welcome to Xhuma</title>
        </head>
        <body>
            <h3>Xhuma</h3>
            <p>This is the internet facing demo for Xhuma</p>
            <p>Interactive API documentation is available <a href="/docs#/">here</a>
            <h4>Endpoints</h4>
            <p>/pds/lookuppatient/nhsno will perform a pds lookup and return the fhir response.
               <a href="pds/lookup_patient/9449306680">Example</a></p>
            <p>/gpconnect/nhsno will perform a gpconnect access record structured query,
               convert it to a CCDA and return the cached record uuid.
               <a href="gpconnect/9690937278">Example</a></p>
            <p>For the purposes of the internet facing demo /demo/nhsno will return the
               mime encoded ccda. <a href="/demo/9690937278">Example</a></p>
        </body>
    </html>
    """


@app.get("/demo/{nhsno}")
async def demo(nhsno: int):
    """
    Demo endpoint that retrieves and returns a CCDA document for a given NHS number.

    Args:
        nhsno (int): NHS number to retrieve the CCDA document for.

    Returns:
        bytes: MIME encoded CCDA document retrieved from Redis cache.
    """
    audit_dict = {
        "subject_id": "CONE, Stephen",
        "organization": "UCLH - University College London Hospitals - TST",
        "organization_id": "urn:oid:1.2.840.114350.1.13.525.3.7.3.688884.100",
        "home_community_id": "urn:oid:1.2.840.114350.1.13.525.3.7.3.688884.100",
        "role": {
            "Role": {
                "@codeSystem": "2.16.840.1.113883.6.96",
                "@code": "224608005",
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
            },
        },
        "resource_id": "9690937278^^^&2.16.840.1.113883.2.1.4.1&ISO",
    }
    bundle_id = await gpconnect(nhsno, audit_dict)
    return redis_client.get(bundle_id["document_id"])


@app.get("/jwk")
async def get_jwk():
    """
    Public endpoint that provides access to the service's JSON Web Key.

    This endpoint is used by clients to verify JWT signatures and establish
    trust with the service.

    Returns:
        dict: JSON Web Key in dictionary format.
    """
    with open("keys/jwk.json", "r") as jwk_file:
        key = json.load(jwk_file)
    return key
