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
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from jwcrypto import jwk
from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .audit.models import _subject_ref_from_nhs_number
from .db import make_engine, make_sessionmaker
from .gpconnect import gpconnect
from .pds import pds
from .redis_connect import redis_client
from .relay import routes
from .relay.hub import WebSocketHub
from .settings import USE_RELAY
from .soap import soap

# Generate or retrieve registry ID from environment
REGISTRY_ID = os.getenv("REGISTRY_ID", str(uuid4()))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context for FastAPI. Runs startup logic before app starts serving.
    """
    # --- Startup logic ---
    # Initialize Postgres connection pool
    engine = make_engine()
    SessionLocal = make_sessionmaker(engine)
    
    app.state.engine = engine
    app.state.SessionLocal = SessionLocal

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

    # Set up OpenTelemetry metrics
    otlp_endpoint = os.getenv(
        "OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317"
    )
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

    # meter = metrics.get_meter("xhuma.business", "1.0.0")
    # app.state.metrics = build_business_metrics(meter)
    try:
        yield
    finally:
        # --- Shutdown logic ---
        # meter_provider.shutdown()
        await engine.dispose()


# Initialize FastAPI application
app = FastAPI(
    title="Xhuma",
    description="A stateless middleware service for GP Connect to CCDA conversion",
    version="1.0.0",
    lifespan=lifespan,
)

# register soap error handler
soap.register_handlers(app)

# 1) Trusted hosts: allow local & your domain
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=[
        "xhumademo.com",
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "*",
    ],  # "*" ok for dev
)

# 2) CORS: allow local & your domain (Starlette applies CORS to WebSockets too)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://xhumademo.com",
        "http://localhost",
        "http://127.0.0.1",
        "http://0.0.0.0",
        "*",
    ],  # "*" ok for dev
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers for different service components
app.include_router(soap.router)
app.include_router(pds.router)

# if using HSCN relay, set up WebSocket hub and routes
if USE_RELAY:
    # Initialize and store WebSocketHub in app state
    app.state.relay_hub = WebSocketHub()
    app.include_router(routes.router)

    # alert that we're using relay
    print("Using HSCN Relay")

else:
    print(f"{USE_RELAY} is not a valid setting for USE_RELAY, defaulting to no relay")


# app.include_router(gpconnect.router)  # Currently disabled
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
async def demo(nhsno: int, request: Request):
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

    bundle_id = await gpconnect(nhsno, audit_dict, request=request)
    # decode jsonresponse

    # gpcon_response = json.loads(bundle_id)  # validate json
    # document_id = gpcon_response.get("document_id")
    return bundle_id


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


# --- Dev-only audit viewer ---
if os.getenv("ENV", "prod").lower() in ("dev", "local"):

    @app.get("/_dev/audit", response_class=HTMLResponse)
    async def dev_audit_form():
        return HTMLResponse(
            """
            <html>
              <head>
                <title>Dev Audit Viewer</title>
                <style>
                  body { font-family: sans-serif; margin: 2rem; }
                  input { padding: 0.5rem; width: 22rem; }
                  button { padding: 0.5rem 1rem; }
                  table { border-collapse: collapse; margin-top: 1rem; width: 100%; }
                  th, td { border: 1px solid #ddd; padding: 0.5rem; font-size: 0.9rem; }
                  th { background: #f5f5f5; text-align: left; }
                  .muted { color: #666; font-size: 0.9rem; }
                </style>
              </head>
              <body>
                <h2>Dev Audit Viewer</h2>
                <p class="muted">Queries by <code>subject_ref</code> derived from NHS number (raw NHS number is not stored).</p>

                <form method="post" action="/_dev/audit">
                  <label>NHS number:</label><br/>
                  <input name="nhs_number" placeholder="e.g. 9690937278" />
                  <button type="submit">Search</button>
                </form>
              </body>
            </html>
            """
        )

    @app.post("/_dev/audit", response_class=HTMLResponse)
    async def dev_audit_query(request: Request, nhs_number: str = Form(...)):
        # Safety: dev only
        secret = os.getenv("API_KEY")
        if not secret:
            return HTMLResponse(
                "<h3>Missing AUDIT_SUBJECT_SECRET</h3>", status_code=500
            )

        try:
            subject_ref = _subject_ref_from_nhs_number(nhs_number, secret)
        except ValueError as e:
            return HTMLResponse(
                f"<h3>Invalid NHS number</h3><p>{e}</p>", status_code=400
            )

        pg = getattr(request.app.state, "pg", None)
        if pg is None:
            return HTMLResponse(
                "<h3>Postgres pool not configured</h3>", status_code=500
            )

        # Keep the fields minimal and safe for display
        sql = """
        SELECT
        event_time,
        sequence,
        action,
        outcome,
        error_code,
        user_id,
        user_role_name,
        user_org_name,
        organisation,
        request_id,
        trace_id,
        document_id,
        message_id
        FROM audit_event
        WHERE subject_ref = $1
        ORDER BY sequence DESC
        LIMIT 500;
        """

        def esc(s: str) -> str:
            return (
                (s or "")
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
                .replace("'", "&#39;")
            )

        try:
            async with pg.acquire() as conn:
                rows = await conn.fetch(sql, subject_ref)
        except Exception as e:
            return HTMLResponse(
                f"<h3>Query failed</h3><pre>{esc(repr(e))}</pre>",
                status_code=500,
            )

        # Render
        out = []
        out.append(
            "<html><head><title>Dev Audit Results</title></head><body style='font-family:sans-serif;margin:2rem;'>"
        )
        out.append("<a href='/_dev/audit'>← back</a>")
        out.append("<h2>Audit results</h2>")
        out.append(f"<p class='muted'>subject_ref: <code>{esc(subject_ref)}</code></p>")

        out.append(f"<p>{len(rows)} row(s) returned (max 500).</p>")

        out.append("<table>")
        out.append(
            "<tr>"
            "<th>Time (UTC)</th><th>Seq</th><th>Action</th><th>Outcome</th><th>Error</th>"
            "<th>User</th><th>Role</th><th>Org</th>"
            "<th>Request ID</th><th>Trace ID</th><th>Doc ID</th><th>Msg ID</th>"
            "</tr>"
        )

        for r in rows:
            out.append(
                "<tr>"
                f"<td>{esc(str(r['event_time']))}</td>"
                f"<td>{esc(str(r['sequence']))}</td>"
                f"<td>{esc(r['action'])}</td>"
                f"<td>{esc(r['outcome'])}</td>"
                f"<td>{esc(r['error_code'] or '')}</td>"
                f"<td>{esc(r['user_id'] or '')}</td>"
                f"<td>{esc(r['user_role_name'] or '')}</td>"
                f"<td>{esc(r['organisation'] or '')}</td>"
                f"<td>{esc(r['request_id'] or '')}</td>"
                f"<td>{esc(r['trace_id'] or '')}</td>"
                f"<td>{esc(r['document_id'] or '')}</td>"
                f"<td>{esc(r['message_id'] or '')}</td>"
                "</tr>"
            )

        out.append("</table></body></html>")
        return HTMLResponse("".join(out))
