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

from .gpconnect import gpconnect
from .pds import pds
from .redis_connect import redis_client
from .soap import soap
from .security import register_jwk_endpoint

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

# Register the JWK endpoints from the security module
register_jwk_endpoint(app)

# Health and readiness endpoints for Azure Container Apps
@app.get("/health")
async def health_check():
    """
    Health check endpoint for container orchestration systems.
    Used by Azure Container Apps liveness probe.
    
    Returns:
        dict: Status indicating the service is healthy
    """
    return {"status": "healthy"}

@app.get("/ready")
async def readiness_check():
    """
    Readiness check endpoint for container orchestration systems.
    Used by Azure Container Apps readiness probe.
    
    This checks if the application is ready to handle traffic.
    
    Returns:
        dict: Status indicating the service is ready to receive requests
    """
    # Check Redis connection
    try:
        redis_client.ping()
        redis_status = "connected"
    except Exception:
        redis_status = "disconnected"
        
    # Check for required environment variables
    env_vars = {
        "JWTKEY": "present" if os.getenv("JWTKEY") else "missing",
    }
    
    # Overall readiness - consider ready even if some components are degraded
    # This allows for graceful degradation in production
    is_ready = True
    
    return {
        "status": "ready" if is_ready else "not_ready",
        "components": {
            "redis": redis_status,
            "environment": env_vars
        }
    }


@app.on_event("startup")
async def startup_event():
    """
    Startup event handler that initializes necessary configurations.

    This function:
    1. Sets the registry ID in Redis
    """
    # Store registry ID in Redis with 24 hour expiry
    redis_client.setex("registry", 86400, str(REGISTRY_ID).encode())


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
    bundle_id = await gpconnect(nhsno)
    return redis_client.get(bundle_id["document_id"])


# Note: The /jwk and /jwks endpoints are now registered through the security module
