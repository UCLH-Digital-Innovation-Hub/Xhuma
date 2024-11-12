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
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
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
    setup_request_context,
    CorrelationManager
)

# Configure logging
logging.config.dictConfig(LOGGING_CONFIG)
logger = get_logger(__name__)

# Initialize metrics
REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total count of HTTP requests",
    ["method", "endpoint", "status_code"]
)

REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "endpoint"]
)

ITI_TRANSACTION_COUNT = Counter(
    "iti_transaction_total",
    "Total count of ITI transactions",
    ["type", "status"]
)

CCDA_CONVERSION_DURATION = Histogram(
    "ccda_conversion_duration_seconds",
    "CCDA conversion duration in seconds"
)

# Initialize OpenTelemetry
resource = Resource.create(attributes={
    "service.name": "xhuma",
    "service.version": "1.0.0",
    "deployment.environment": os.getenv("ENVIRONMENT", "production")
})

trace.set_tracer_provider(TracerProvider(resource=resource))
otlp_exporter = OTLPSpanExporter(
    endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317"),
    insecure=True
)
span_processor = BatchSpanProcessor(otlp_exporter)
trace.get_tracer_provider().add_span_processor(span_processor)

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

class MetricsMiddleware(BaseHTTPMiddleware):
    """Middleware for collecting request metrics."""
    
    async def dispatch(self, request: Request, call_next):
        """Process the request and collect metrics."""
        method = request.method
        path = request.url.path
        
        with REQUEST_LATENCY.labels(method=method, endpoint=path).time():
            response = await call_next(request)
            
            REQUEST_COUNT.labels(
                method=method,
                endpoint=path,
                status_code=response.status_code
            ).inc()
            
            return response

class CorrelationMiddleware(BaseHTTPMiddleware):
    """Middleware to handle correlation IDs for request tracing."""
    
    async def dispatch(self, request: Request, call_next):
        """Process the request and add correlation ID."""
        correlation_id = request.headers.get(
            CORRELATION_ID_CONFIG["header_name"],
            str(uuid4())
        )
        
        request.state.correlation_id = correlation_id
        
        with setup_request_context(correlation_id):
            response = await call_next(request)
            response.headers[CORRELATION_ID_CONFIG["header_name"]] = correlation_id
            return response

# Add middleware
app.add_middleware(MetricsMiddleware)
app.add_middleware(CorrelationMiddleware)

# Initialize OpenTelemetry instrumentation
FastAPIInstrumentor.instrument_app(app)

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

@app.get("/metrics")
async def metrics():
    """
    Endpoint that exposes Prometheus metrics.
    
    :return: Prometheus metrics in text format
    :rtype: Response
    """
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

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
        with CCDA_CONVERSION_DURATION.time():
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
