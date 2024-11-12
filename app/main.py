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
import logging.config

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi_observability import Instrumentator
from starlette.middleware.base import BaseHTTPMiddleware
from fhirclient.models import bundle
from jwcrypto import jwk

from .gpconnect import gpconnect
from .pds import pds
from .redis_connect import redis_client
from .soap import soap
from .config import (
    LOGGING_CONFIG, 
    FASTAPI_OBSERVABILITY_CONFIG,
    CORRELATION_ID_CONFIG,
    get_logger
)
from .handlers import (
    ContextualFilter,
    setup_request_context
)

# Configure logging
logging.config.dictConfig(LOGGING_CONFIG)
logger = get_logger(__name__)

# Add contextual filter to root logger
logging.getLogger("xhuma").addFilter(ContextualFilter())

# Initialize FastAPI application
app = FastAPI(
    title="Xhuma",
    description="A stateless middleware service for GP Connect to CCDA conversion",
    version="1.0.0",
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class CorrelationMiddleware(BaseHTTPMiddleware):
    """Middleware to handle correlation IDs for request tracing."""
    
    async def dispatch(self, request: Request, call_next):
        """
        Process the request and add correlation ID.
        
        :param request: The incoming request
        :type request: Request
        :param call_next: The next middleware in the chain
        :return: The response with correlation ID header
        """
        correlation_id = request.headers.get(
            CORRELATION_ID_CONFIG["header_name"],
            CORRELATION_ID_CONFIG["generator"]()
        )
        
        request.state.correlation_id = correlation_id
        
        with setup_request_context(correlation_id):
            response = await call_next(request)
            response.headers[CORRELATION_ID_CONFIG["header_name"]] = correlation_id
            return response

# Add correlation ID middleware
app.add_middleware(CorrelationMiddleware)

# Initialize FastAPI Observability
instrumentator = Instrumentator(
    should_group_status_codes=FASTAPI_OBSERVABILITY_CONFIG["should_group_status_codes"],
    should_ignore_untemplated=FASTAPI_OBSERVABILITY_CONFIG["should_ignore_untemplated"],
    should_group_untemplated=FASTAPI_OBSERVABILITY_CONFIG["should_group_untemplated"],
    should_round_latency_decimals=FASTAPI_OBSERVABILITY_CONFIG["should_round_latency_decimals"],
    excluded_handlers=FASTAPI_OBSERVABILITY_CONFIG["excluded_handlers"],
    buckets=FASTAPI_OBSERVABILITY_CONFIG["buckets"],
    should_include_handler_name=FASTAPI_OBSERVABILITY_CONFIG["should_include_handler_name"],
    should_include_method=FASTAPI_OBSERVABILITY_CONFIG["should_include_method"],
    should_include_status=FASTAPI_OBSERVABILITY_CONFIG["should_include_status"],
    should_include_hostname=FASTAPI_OBSERVABILITY_CONFIG["should_include_hostname"],
    hostname_label=FASTAPI_OBSERVABILITY_CONFIG["hostname_label"],
    label_names=FASTAPI_OBSERVABILITY_CONFIG["label_names"],
    namespace=FASTAPI_OBSERVABILITY_CONFIG["namespace"],
    subsystem=FASTAPI_OBSERVABILITY_CONFIG["subsystem"],
)
instrumentator.instrument(app).expose(
    app,
    endpoint=FASTAPI_OBSERVABILITY_CONFIG["metrics_route"],
    name=FASTAPI_OBSERVABILITY_CONFIG["metrics_route_name"],
    format=FASTAPI_OBSERVABILITY_CONFIG["metrics_route_format"],
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
    
    * Sets the registry ID in Redis
    * Checks for existing JWK (JSON Web Key)
    * Generates a new JWK from private key if none exists
    """
    logger.info("Starting Xhuma application", extra={"registry_id": REGISTRY_ID})
    
    redis_client.set("registry", REGISTRY_ID)
    
    if not os.path.isfile("keys/jwk.json"):
        logger.info("Generating new JWK from private key")
        try:
            with open("keys/test-1.pem", "rb") as pemfile:
                private_pem = pemfile.read()
                public_jwk = jwk.JWK.from_pem(data=private_pem)
                jwk_json = public_jwk.export_public(as_dict=True)
                
                with open("keys/jwk.json", "w") as f:
                    json.dump(jwk_json, f)
            logger.info("Successfully generated and saved JWK")
        except Exception as e:
            logger.error(f"Failed to generate JWK: {str(e)}")
            raise

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup handler for application shutdown."""
    logger.info("Shutting down Xhuma application")

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def root():
    """
    Root endpoint that serves the welcome page with service information.
    
    :return: A welcome page containing service description and endpoint examples
    :rtype: HTMLResponse
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
async def demo(nhsno: int, request: Request):
    """
    Demo endpoint that retrieves and returns a CCDA document for a given NHS number.
    
    :param nhsno: NHS number to retrieve the CCDA document for
    :type nhsno: int
    :param request: FastAPI request object containing correlation ID
    :type request: Request
    :return: MIME encoded CCDA document retrieved from Redis cache
    :rtype: bytes
    """
    logger.info(
        f"Processing demo request for NHS number: {nhsno}",
        extra={
            "correlation_id": request.state.correlation_id,
            "nhs_number": str(nhsno)
        }
    )
    
    try:
        bundle_id = await gpconnect(nhsno)
        return redis_client.get(bundle_id["document_id"])
    except Exception as e:
        logger.error(
            f"Error processing demo request: {str(e)}",
            extra={
                "correlation_id": request.state.correlation_id,
                "nhs_number": str(nhsno)
            }
        )
        raise

@app.get("/jwk")
async def get_jwk(request: Request):
    """
    Public endpoint that provides access to the service's JSON Web Key.
    
    This endpoint is used by clients to verify JWT signatures and establish
    trust with the service.
    
    :param request: FastAPI request object containing correlation ID
    :type request: Request
    :return: JSON Web Key in dictionary format
    :rtype: dict
    """
    logger.info(
        "JWK request received",
        extra={"correlation_id": request.state.correlation_id}
    )
    
    try:
        with open("keys/jwk.json", "r") as jwk_file:
            key = json.load(jwk_file)
        return key
    except Exception as e:
        logger.error(
            f"Error retrieving JWK: {str(e)}",
            extra={"correlation_id": request.state.correlation_id}
        )
        raise
