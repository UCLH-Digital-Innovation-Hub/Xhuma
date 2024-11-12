FROM python:3.10

WORKDIR /code

# Install system dependencies
RUN apt-get update && apt-get install -y \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Create necessary directories
RUN mkdir -p /code/logs /code/keys

# Copy application code
COPY app /code/app
COPY keys/test-1.pem /code/keys/test-1.pem

# Install Python dependencies directly with pip
RUN pip install fastapi pydantic pyjwt httpx xmltodict uvicorn[standard] fhirclient==3.0.0 \
    redis cryptography jwcrypto prometheus-client opentelemetry-api opentelemetry-sdk \
    opentelemetry-instrumentation-fastapi opentelemetry-exporter-otlp python-json-logger psycopg2-binary \
    pytest httpx

# Set environment variables for OpenTelemetry
ENV OTEL_PYTHON_METER_PROVIDER="sdk_meter_provider"
ENV OTEL_PYTHON_TRACER_PROVIDER="sdk_tracer_provider"
ENV OTEL_METRICS_EXPORTER="prometheus"
ENV OTEL_TRACES_EXPORTER="otlp"
ENV OTEL_EXPORTER_OTLP_ENDPOINT="http://otel-collector:4317"
ENV OTEL_RESOURCE_ATTRIBUTES="service.name=xhuma,deployment.environment=production"

# Set permissions
RUN chmod -R 755 /code/logs

# Run the application
CMD uvicorn app.main:app --host=0.0.0.0 --port=${PORT:-80}
