"""
Xhuma Main Application Module

This module serves as the entry point for the Xhuma middleware service, which facilitates
the conversion of GP Connect structured records into CCDA format. It initializes the FastAPI
application, sets up routers, and handles startup configuration including JWT key management.

The service implements a stateless architecture with Redis caching and supports IHE ITI
profiles for healthcare interoperability.
"""

import asyncio
import datetime
import logging
import math
import os
import tempfile
import traceback
import uuid
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from uuid import uuid4

# Configure Azure Monitor OpenTelemetry if connection string is present
if os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"):
    from azure.monitor.opentelemetry import configure_azure_monitor

    configure_azure_monitor()

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fhirclient.models.operationoutcome import OperationOutcome, OperationOutcomeIssue
from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from sqlalchemy import select

from .audit.audit import AuditFailureException
from .audit.db_models import AuditEventRow
from .audit.models import _subject_ref_from_nhs_number
from .db import make_engine, make_sessionmaker
from .middleware.mtls import MTLSMiddleware
from .redis_connect import redis_client, snomed_client
from .relay import routes
from .relay.hub import WebSocketHub
from .security import fix_pem_formatting, get_jwk_keyset, verify_api_key
from .settings import USE_RELAY, diag_compare_enabled, diag_ttl_minutes
from .soap import soap
from .telemetry import monitor_event_loop

# Generate or retrieve registry ID from environment
REGISTRY_ID = os.getenv("REGISTRY_ID", str(uuid4()))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context for FastAPI. Runs startup logic before app starts serving.
    """
    # --- Startup logic ---
    # Validate required configuration
    for var in ["API_KEY", "NHS_API_KEY", "ORG_ASID", "ORG_CODE"]:
        val = os.getenv(var)
        if not val or not val.strip():
            raise RuntimeError(f"Missing required configuration: {var}")
        if val.strip().startswith("@Microsoft.KeyVault("):
            raise RuntimeError(f"Unresolved KeyVault reference for required configuration: {var}")

    # Check configuration separation
    api_key = os.getenv("API_KEY")
    nhs_api_key = os.getenv("NHS_API_KEY")
    is_prod = os.getenv("ENV", "prod").lower() == "prod"

    if api_key and nhs_api_key and api_key == nhs_api_key:
        if is_prod:
            raise RuntimeError("CRITICAL: API_KEY and NHS_API_KEY must not be identical in production. Failing closed.")
        else:
            print("Warning: API_KEY and NHS_API_KEY are identical. This is allowed in dev/int but forbidden in prod.")

    ccda_expiry_str = os.getenv("CCDA_EXPIRY_HOURS", "4")
    try:
        expiry_hours = float(ccda_expiry_str)
        if not math.isfinite(expiry_hours) or expiry_hours <= 0:
            raise ValueError("Must be positive and finite")
        ttl = datetime.timedelta(hours=expiry_hours)
        if ttl.total_seconds() < 1:
            raise ValueError("Duration too small for Redis expiry")
        # Prevent integer overflow in Redis / Timedelta by capping at a reasonable upper bound (e.g. 1 year)
        if ttl.total_seconds() > 31536000:
            raise ValueError("Duration exceeds maximum allowed cache expiry (1 year)")

        app.state.ccda_expiry_hours = expiry_hours
    except (ValueError, OverflowError) as e:
        raise RuntimeError(f"Invalid CCDA_EXPIRY_HOURS configuration: {e}")

    async with AsyncExitStack() as resources:
        # Initialize Postgres connection pool
        engine = make_engine()
        resources.push_async_callback(engine.dispose)
        resources.push_async_callback(redis_client.close)
        resources.push_async_callback(snomed_client.close)
        SessionLocal = make_sessionmaker(engine)

        app.state.engine = engine
        app.state.SessionLocal = SessionLocal

        # Store registry ID in Redis with 24 hour expiry
        try:
            await redis_client.setex("registry", 86400, str(REGISTRY_ID).encode())
        except Exception as e:
            print(f"Warning: Failed to connect to Redis during startup: {e}")

        # Handle NHS Certificates securely
        cert_dir_obj = tempfile.TemporaryDirectory()
        resources.callback(cert_dir_obj.cleanup)
        app.state.nhs_cert_dir_obj = cert_dir_obj
        cert_dir = cert_dir_obj.name
        os.chmod(cert_dir, 0o700)
        os.environ["NHS_CERT_DIR_EPHEMERAL"] = cert_dir

        if os.getenv("NHS_CLIENT_CERT") and os.getenv("NHS_CLIENT_KEY") and os.getenv("NHS_BUNDLE"):
            try:
                cert_path = os.path.join(cert_dir, "client_cert.pem")
                key_path = os.path.join(cert_dir, "client_key.pem")
                bundle_path = os.path.join(cert_dir, "nhs_bundle.pem")

                with open(cert_path, "w") as f:
                    f.write(fix_pem_formatting(os.getenv("NHS_CLIENT_CERT")))
                with os.fdopen(os.open(key_path, os.O_WRONLY | os.O_CREAT, 0o600), "w") as f:
                    f.write(fix_pem_formatting(os.getenv("NHS_CLIENT_KEY")))
                with open(bundle_path, "w") as f:
                    f.write(fix_pem_formatting(os.getenv("NHS_BUNDLE")))
                print("Successfully hydrated NHS certificates to ephemeral filesystem storage.")
            except Exception:
                print(
                    "Warning: Failed to hydrate NHS certificates to ephemeral storage (malformed material). GP Connect will fail closed."
                )
        else:
            if os.getenv("ENV", "prod").lower() == "prod" and os.getenv("USE_RELAY") not in ("1", "true", "True"):
                print(
                    "Warning: Missing NHS_CLIENT_CERT, NHS_CLIENT_KEY or NHS_BUNDLE in production. Direct GP Connect will fail closed."
                )

        # Handle JWK generation/verification securely entirely in-memory
        app.state.jwk_json = {}
        app.state.jwk_keys = []

        try:
            keys = await asyncio.to_thread(get_jwk_keyset)
            if keys:
                app.state.jwk_keys = keys
                app.state.jwk_json = keys[0]
            else:
                logging.warning("No JWT signing key configured; /jwk will return an error")
        except Exception as e:
            logging.warning(f"Failed to load JWT signing key during startup: {e}")

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

        # meter = metrics.get_meter("xhuma.business", "1.0.0")
        # app.state.metrics = build_business_metrics(meter)
        loop_monitor = asyncio.create_task(monitor_event_loop())
        try:
            yield
        finally:
            loop_monitor.cancel()
            with suppress(asyncio.CancelledError):
                await loop_monitor
            # --- Shutdown logic ---
            # meter_provider.shutdown()


# Initialize FastAPI application
app = FastAPI(
    title="Xhuma",
    description="A stateless middleware service for GP Connect to CCDA conversion",
    version="0.9.2",
    lifespan=lifespan,
)
# Instrument FastAPI app, HTTPX client, and Logging for Azure Application Insights
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


# register soap error handler
soap.register_handlers(app)


@app.exception_handler(AuditFailureException)
async def audit_failure_handler(request: Request, exc: AuditFailureException):
    trace_id = str(uuid.uuid4())
    # Explicitly do NOT log traceback to avoid leaking SQL parameters
    logging.error(f"AuditFailureException [TraceID: {trace_id}] at {request.url.path}: {exc}")

    path = request.url.path
    if path.startswith("/SOAP") or path.startswith("/iti") or "soap" in path.lower():
        return soap.SoapFault(f"Internal Server Error (TraceID: {trace_id})", code="Receiver").response()

    elif path.startswith("/FHIR") or path.startswith("/pds") or "fhir" in path.lower():
        issue = OperationOutcomeIssue()
        issue.severity = "fatal"
        issue.code = "exception"
        issue.diagnostics = f"An internal error occurred. TraceID: {trace_id}"
        outcome = OperationOutcome()
        outcome.issue = [issue]
        return JSONResponse(status_code=502, content=outcome.as_json())

    else:
        return JSONResponse(
            status_code=502,
            content={
                "type": "about:blank",
                "title": "Internal Server Error",
                "status": 502,
                "detail": f"An unexpected error occurred. TraceID: {trace_id}",
            },
        )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    trace_id = str(uuid.uuid4())
    logging.error(f"Unhandled Exception [TraceID: {trace_id}] at {request.url.path}: {exc}\n{traceback.format_exc()}")

    path = request.url.path
    if path.startswith("/SOAP") or path.startswith("/iti") or "soap" in path.lower():
        return soap.SoapFault(f"Internal Server Error (TraceID: {trace_id})", code="Receiver").response()

    elif path.startswith("/FHIR") or path.startswith("/pds") or "fhir" in path.lower():
        issue = OperationOutcomeIssue()
        issue.severity = "fatal"
        issue.code = "exception"
        issue.diagnostics = f"An internal error occurred. TraceID: {trace_id}"
        outcome = OperationOutcome()
        outcome.issue = [issue]
        return JSONResponse(status_code=500, content=outcome.as_json())

    else:
        return JSONResponse(
            status_code=500,
            content={
                "type": "about:blank",
                "title": "Internal Server Error",
                "status": 500,
                "detail": f"An unexpected error occurred. TraceID: {trace_id}",
            },
        )


# 1) Trusted hosts
allowed_hosts_str = os.getenv("ALLOWED_HOSTS", "*")
allowed_hosts = [h.strip() for h in allowed_hosts_str.split(",")]

app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=allowed_hosts,
)

# 2) CORS
cors_origins_str = os.getenv("CORS_ORIGINS", "*")
allow_origins = [o.strip() for o in cors_origins_str.split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 3) mTLS Middleware
app.add_middleware(MTLSMiddleware)

# Include routers for different service components
app.include_router(soap.router)


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
        </body>
    </html>
    """


@app.get("/health")
async def health_check():
    """
    Public health check endpoint.
    """
    return {"status": "ok"}


@app.get("/jwk")
async def get_jwk(request: Request):
    """
    Public endpoint that provides the service's JSON Web Key Set.

    This endpoint is used by clients to verify JWT signatures and establish
    trust with the service.

    Returns:
        dict: JSON Web Key Set in dictionary format.
    """
    try:
        keys = await asyncio.to_thread(get_jwk_keyset)
        if keys:
            if hasattr(request, "app") and hasattr(request.app, "state"):
                request.app.state.jwk_keys = keys
                request.app.state.jwk_json = keys[0]
            return {"keys": keys}
    except Exception as e:
        logging.warning(f"Failed to load JWT signing key: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="JWKS unavailable",
        )

    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="JWKS unavailable",
    )


# --- Dev-only audit viewer ---
if os.getenv("ENV", "prod").lower() in ("dev", "local"):

    @app.get(
        "/_dev/audit",
        response_class=HTMLResponse,
        dependencies=[Depends(verify_api_key)],
    )
    async def dev_audit_form():
        return HTMLResponse("""
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
            """)

    @app.post(
        "/_dev/audit",
        response_class=HTMLResponse,
        dependencies=[Depends(verify_api_key)],
    )
    async def dev_audit_query(request: Request, nhs_number: str = Form(...)):
        # --- safety: dev only ---
        secret = os.getenv("API_KEY")
        if not secret:
            return HTMLResponse(
                "<h3>Missing API_KEY</h3>",
                status_code=500,
            )

        try:
            subject_ref = _subject_ref_from_nhs_number(nhs_number, secret)
        except ValueError as e:
            return HTMLResponse(
                f"<h3>Invalid NHS number</h3><p>{e}</p>",
                status_code=400,
            )

        SessionLocal = getattr(request.app.state, "SessionLocal", None)
        if SessionLocal is None:
            return HTMLResponse(
                "<h3>Database session not configured</h3>",
                status_code=500,
            )

        stmt = (
            select(AuditEventRow)
            .where(AuditEventRow.subject_ref == subject_ref)
            .order_by(AuditEventRow.sequence.desc())
            .limit(500)
        )

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
            async with SessionLocal() as session:  # type: AsyncSession
                result = await session.execute(stmt)
                rows = result.scalars().all()
        except Exception as e:
            return HTMLResponse(
                f"<h3>Query failed</h3><pre>{esc(repr(e))}</pre>",
                status_code=500,
            )

        # --- render ---
        out: list[str] = []
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
            "<th>Request ID</th><th>Trace ID</th>"
            "<th>Doc ID</th><th>Msg ID</th>"
            "</tr>"
        )

        for r in rows:
            out.append(
                "<tr>"
                f"<td>{esc(str(r.event_time))}</td>"
                f"<td>{esc(str(r.sequence))}</td>"
                f"<td>{esc(r.action)}</td>"
                f"<td>{esc(r.outcome)}</td>"
                f"<td>{esc(r.error_code or '')}</td>"
                f"<td>{esc(r.user_id or '')}</td>"
                f"<td>{esc(r.user_role_name or '')}</td>"
                f"<td>{esc(r.organisation or '')}</td>"
                f"<td>{esc(r.request_id or '')}</td>"
                f"<td>{esc(r.trace_id or '')}</td>"
                f"<td>{esc(r.document_id or '')}</td>"
                f"<td>{esc(r.message_id or '')}</td>"
                "</tr>"
            )

        out.append("</table></body></html>")
        return HTMLResponse("".join(out))


# --- Diagnostic FHIR-vs-XML comparison viewer ---
# Gated by settings.diag_compare_enabled(): always on in dev/local/int, opt-in in prd via
# DIAG_COMPARE_ENABLED. Protected by the same API-key dependency as /_dev/audit. Both the FHIR
# bundle and the C-CDA are PHI; they are read from the short-lived diag:* Redis keys written during
# conversion (gpconnect._fetch_gpconnect_record) and expire on their own after DIAG_TTL_MIN.
if diag_compare_enabled():
    import difflib
    import html as _html

    _DIAG_VIEWS = ("split", "tabs", "diff")

    _DIAG_STYLE = """
    <style>
      body { font-family: -apple-system, system-ui, sans-serif; margin: 1.5rem; color: #1a1a1a; }
      h2 { margin: 0 0 0.25rem; }
      .muted { color: #666; font-size: 0.85rem; }
      nav { margin: 0.5rem 0 0.75rem; }
      nav a { margin-right: 0.6rem; }
      nav a.active { font-weight: 700; text-decoration: none; }
      form input { padding: 0.4rem; width: 16rem; }
      form button { padding: 0.4rem 0.9rem; }
      .cols { display: flex; gap: 1rem; align-items: stretch; }
      .col { flex: 1 1 0; min-width: 0; }
      .col h3 { margin: 0.25rem 0; font-size: 0.95rem; }
      pre { background: #f6f8fa; border: 1px solid #ddd; border-radius: 4px; padding: 0.75rem;
            overflow: auto; max-height: 78vh; font-size: 0.8rem; white-space: pre; }
      .tabbtn { padding: 0.4rem 0.9rem; margin-right: 0.3rem; cursor: pointer; border: 1px solid #ccc;
                background: #eee; border-radius: 4px; }
      .tabbtn.active { background: #0a7f46; color: #fff; border-color: #0a7f46; }
      table.diff { font-family: monospace; font-size: 0.75rem; border-collapse: collapse; width: 100%; }
      td.diff_header { text-align: right; color: #999; }
      .diff_add { background: #dfd; }
      .diff_chg { background: #ffd; }
      .diff_sub { background: #fdd; }
    </style>
    """

    _DIAG_FORM = (
        "<html><head><title>Xhuma diagnostics: FHIR vs XML</title>" + _DIAG_STYLE + "</head><body>"
        "<h2>FHIR &rarr; C-CDA comparison</h2>"
        "<p class='muted'>Shows the FHIR bundle as received from GP Connect next to the C-CDA XML as "
        "sent to Epic. Diagnostics expire " + str(diag_ttl_minutes()) + " min after a record is fetched.</p>"
        "<form method='get' action='/_diag/compare'>"
        "<label>NHS number:</label> <input name='nhsno' placeholder='e.g. 9690937278' autofocus/> "
        "<button type='submit'>Compare</button></form>"
        "<p class='muted'>Or pass <code>?doc_uuid=&lt;id&gt;</code>. Add <code>&amp;view=split|tabs|diff</code>.</p>"
        "</body></html>"
    )

    def _diag_decode(raw):
        if raw is None:
            return None
        if isinstance(raw, (bytes, bytearray)):
            return raw.decode("utf-8", "replace")
        return str(raw)

    def _diag_message(title: str, body_html: str) -> HTMLResponse:
        return HTMLResponse(
            "<html><head><title>Xhuma diagnostics</title>" + _DIAG_STYLE + "</head><body>"
            "<a href='/_diag/compare'>&larr; back</a><h2>" + title + "</h2>"
            "<p class='muted'>" + body_html + "</p></body></html>"
        )

    @app.get(
        "/_diag/compare",
        response_class=HTMLResponse,
        include_in_schema=False,
        dependencies=[Depends(verify_api_key)],
    )
    async def diag_compare(nhsno: str | None = None, doc_uuid: str | None = None, view: str = "split"):
        """Render the received FHIR bundle alongside the C-CDA XML sent to Epic.

        Resolve order: explicit ``doc_uuid`` wins, else ``nhsno`` is mapped via the cached
        ``nhsno -> doc_uuid`` key. ``view`` is one of split (default), tabs, diff.
        """
        view = view if view in _DIAG_VIEWS else "split"
        if not nhsno and not doc_uuid:
            return HTMLResponse(_DIAG_FORM)

        if doc_uuid:
            resolved = doc_uuid.strip()
        else:
            try:
                nhs_key = str(int(nhsno))
            except (TypeError, ValueError):
                return _diag_message("Invalid NHS number", "Expected digits only.")
            resolved = _diag_decode(await redis_client.get(nhs_key))
            if not resolved:
                return _diag_message(
                    "No record cached",
                    "No document is currently cached for that NHS number. Fetch the patient via "
                    "ITI-38 first, then retry within the diagnostics window.",
                )

        bundle_text = _diag_decode(await redis_client.get(f"diag:bundle:{resolved}"))
        xml_text = _diag_decode(await redis_client.get(f"diag:xml:{resolved}"))
        if bundle_text is None and xml_text is None:
            return _diag_message(
                "Diagnostics expired or not captured",
                "No diagnostic pair for doc_uuid <code>"
                + _html.escape(resolved)
                + "</code>. They expire "
                + str(diag_ttl_minutes())
                + " min after capture and are only written when the diagnostics "
                "gate is enabled. Re-fetch the patient and retry.",
            )

        bundle_text = bundle_text if bundle_text is not None else "(FHIR bundle not captured)"
        xml_text = xml_text if xml_text is not None else "(C-CDA XML not captured)"
        esc_bundle = _html.escape(bundle_text)
        esc_xml = _html.escape(xml_text)

        qbase = "/_diag/compare?doc_uuid=" + _html.escape(resolved, quote=True)

        def navlink(v: str, label: str) -> str:
            cls = " class='active'" if v == view else ""
            return "<a href='" + qbase + "&amp;view=" + v + "'" + cls + ">" + label + "</a>"

        header = (
            "<h2>FHIR &rarr; C-CDA comparison</h2>"
            "<p class='muted'>doc_uuid <code>" + _html.escape(resolved) + "</code></p>"
            "<nav><a href='/_diag/compare'>&larr; new</a> &nbsp;|&nbsp; View: "
            + navlink("split", "Side-by-side")
            + navlink("tabs", "Two pages")
            + navlink("diff", "Diff")
            + "</nav>"
        )

        if view == "tabs":
            body = (
                "<div style='margin:0.5rem 0;'>"
                "<button id='tab-bundle' class='tabbtn active' onclick=\"diagShow('bundle')\">FHIR bundle (received)</button>"
                "<button id='tab-xml' class='tabbtn' onclick=\"diagShow('xml')\">C-CDA XML (sent to Epic)</button>"
                "</div>"
                "<div id='pane-bundle'><pre>" + esc_bundle + "</pre></div>"
                "<div id='pane-xml' style='display:none;'><pre>" + esc_xml + "</pre></div>"
                "<script>function diagShow(w){"
                "document.getElementById('pane-bundle').style.display=w==='bundle'?'block':'none';"
                "document.getElementById('pane-xml').style.display=w==='xml'?'block':'none';"
                "document.getElementById('tab-bundle').classList.toggle('active',w==='bundle');"
                "document.getElementById('tab-xml').classList.toggle('active',w==='xml');}</script>"
            )
        elif view == "diff":
            body = difflib.HtmlDiff(wrapcolumn=100).make_table(
                bundle_text.splitlines(),
                xml_text.splitlines(),
                fromdesc="FHIR bundle (received)",
                todesc="C-CDA XML (sent to Epic)",
                context=False,
            )
        else:  # split
            body = (
                "<div class='cols'>"
                "<div class='col'><h3>FHIR bundle (received)</h3><pre>" + esc_bundle + "</pre></div>"
                "<div class='col'><h3>C-CDA XML (sent to Epic)</h3><pre>" + esc_xml + "</pre></div>"
                "</div>"
            )

        return HTMLResponse(
            "<html><head><title>Xhuma diagnostics: FHIR vs XML</title>"
            + _DIAG_STYLE
            + "</head><body>"
            + header
            + body
            + "</body></html>"
        )
