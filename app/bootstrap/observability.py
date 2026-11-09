"""Observability setup, invoked explicitly at the original bootstrap phases."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress


def configure_import_observability():
    # Keep Azure initialization ahead of application imports and unguarded.
    if os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"):
        from azure.monitor.opentelemetry import configure_azure_monitor

        configure_azure_monitor()


def configure_application_observability(app):
    if os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"):
        try:
            # Ensure the root logger captures INFO logs so they are exported
            # Keep these optional imports conditional and failure-tolerant: telemetry
            # configuration must not prevent the application from starting.
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
            from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
            from opentelemetry.instrumentation.logging import LoggingInstrumentor
            from opentelemetry.instrumentation.redis import RedisInstrumentor

            logging.getLogger().setLevel(logging.INFO)

            FastAPIInstrumentor.instrument_app(app)
            HTTPXClientInstrumentor().instrument()
            LoggingInstrumentor().instrument(set_logging_format=True)
            RedisInstrumentor().instrument()
            print("Application Insights OpenTelemetry instrumentation enabled successfully.")
        except Exception as telemetry_err:
            print(f"Warning: Failed to initialize OpenTelemetry instrumentation: {telemetry_err}")


@asynccontextmanager
async def lifespan_observability():
    # These imports must not precede Azure's import-time bootstrap.
    from opentelemetry import metrics
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

    from app.telemetry import monitor_event_loop

    # Set up OpenTelemetry metrics
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
    metric_exporter = OTLPMetricExporter(
        endpoint=otlp_endpoint.replace("http://", "").replace("https://", ""),
        insecure=True,
    )

    reader = PeriodicExportingMetricReader(
        exporter=metric_exporter,
        export_interval_millis=int(os.getenv("OTEL_METRIC_EXPORT_INTERVAL_MS", "5000")),
    )
    meter_provider = MeterProvider(metric_readers=[reader])
    metrics.set_meter_provider(meter_provider)

    loop_monitor = asyncio.create_task(monitor_event_loop())
    try:
        yield
    finally:
        loop_monitor.cancel()
        with suppress(asyncio.CancelledError):
            await loop_monitor
        # --- Shutdown logic ---
        # meter_provider.shutdown()
