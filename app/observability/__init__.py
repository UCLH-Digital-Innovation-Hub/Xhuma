"""
OpenTelemetry Observability Initialization Module

This module initializes OpenTelemetry tracing for the Xhuma application,
configuring the OTLP exporter to send traces to the OTEL Collector.
"""

import os
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from fastapi import FastAPI


def init_observability(app: FastAPI) -> None:
    """
    Initialize OpenTelemetry tracing for the FastAPI application.
    
    Configures:
    - Service resource with name and version
    - OTLP exporter to send traces to OTEL Collector
    - Global tracer provider
    - FastAPI auto-instrumentation
    
    Args:
        app: The FastAPI application instance to instrument
    """
    # Get configuration from environment variables
    otel_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
    service_name = os.getenv("OTEL_SERVICE_NAME", "xhuma")
    environment = os.getenv("ENVIRONMENT", "production")
    
    # Create resource with service metadata
    resource = Resource(
        attributes={
            SERVICE_NAME: service_name,
            SERVICE_VERSION: "1.0.0",
            "environment": environment,
        }
    )
    
    # Configure OTLP exporter
    otlp_exporter = OTLPSpanExporter(
        endpoint=otel_endpoint,
        insecure=True,  # Use insecure for internal Docker network
    )
    
    # Create tracer provider with batch span processor
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(otlp_exporter)
    )
    
    # Set as global tracer provider
    trace.set_tracer_provider(tracer_provider)
    
    # Auto-instrument FastAPI
    FastAPIInstrumentor.instrument_app(app)
    
    print(f"✓ OpenTelemetry initialized: {service_name} → {otel_endpoint}")
