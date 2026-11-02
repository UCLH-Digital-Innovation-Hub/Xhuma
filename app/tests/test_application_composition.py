"""Composition compatibility checks, isolated from collection-time app state."""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# Captured from the pre-decomposition app; normalize router nesting, not route order.
BASE_ROUTES = [
    ("/openapi.json", ["GET", "HEAD"], "openapi", False),
    ("/docs", ["GET", "HEAD"], "swagger_ui_html", False),
    ("/docs/oauth2-redirect", ["GET", "HEAD"], "swagger_ui_redirect", False),
    ("/redoc", ["GET", "HEAD"], "redoc_html", False),
    ("/SOAP/iti55", ["POST"], "iti55", True),
    ("/SOAP/iti38", ["POST"], "iti38", True),
    ("/SOAP/iti39", ["POST"], "iti39", True),
    ("/", ["GET"], "root", False),
    ("/health", ["GET"], "health_check", True),
    ("/jwk", ["GET"], "get_jwk", True),
]
DEV_ROUTES = [
    ("/_dev/audit", ["GET"], "dev_audit_form", True),
    ("/_dev/audit", ["POST"], "dev_audit_query", True),
]

PRELUDE = """
import importlib
import os
import sys

def normalized_routes(routes):
    for route in routes:
        # FastAPI 0.162 uses lazy included routers; older versions flatten them.
        if hasattr(route, "effective_candidates"):
            yield from normalized_routes(route.effective_candidates())
            continue
        route = getattr(route, "starlette_route", None) or route
        yield (
            route.path,
            sorted(route.methods) if hasattr(route, "methods") else ["WEBSOCKET"],
            route.name,
            getattr(route, "include_in_schema", False),
        )
"""


def run_isolated(tmp_path, source):
    # Do not inherit developer credentials, telemetry exporters, or test-module env writes.
    env = {name: os.environ[name] for name in ("PATH", "SYSTEMROOT") if name in os.environ}
    env.update(
        PYTHONPATH=str(ROOT),
        PYTHONDONTWRITEBYTECODE="1",
        ENV="dev",  # Initial transitive GP Connect/PDS imports do not support "local".
        USE_RELAY="false",
        REQUIRE_MTLS="false",
        API_KEY="composition-test-key",
        NHS_API_KEY="composition-test-nhs-key",
        ORG_ASID="123",
        ORG_CODE="RRV00",
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", PRELUDE + textwrap.dedent(source)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_routes_environment_gating_and_cached_relay_snapshot(tmp_path):
    run_isolated(
        tmp_path,
        f"""
        import app.main as main
        import app.settings as settings
        from unittest.mock import patch

        base = {BASE_ROUTES!r}
        dev = {DEV_ROUTES!r}
        assert list(normalized_routes(main.app.routes)) == base + dev
        previous = main.app
        for environment in ("dev", "local", "prod", "int"):
            os.environ["ENV"] = environment
            for relay in (False, True):
                # Environment alone must not replace settings' cached snapshot.
                os.environ["USE_RELAY"] = "false" if relay else "true"
                with patch.object(settings, "USE_RELAY", relay):
                    importlib.reload(main)
                assert main.app is not previous
                previous = main.app
                expected = base[:7]
                if relay:
                    expected += [("/relay/ws/{{client_id}}", ["WEBSOCKET"], "relay_ws", False)]
                expected += base[7:]
                if environment in ("dev", "local"):
                    expected += dev
                assert list(normalized_routes(main.app.routes)) == expected
                assert hasattr(main.app.state, "relay_hub") is relay
        """,
    )


def test_metadata_handlers_middleware_and_public_responses(tmp_path):
    run_isolated(
        tmp_path,
        """
        import app.main as main
        from app.version import VERSION
        from fastapi.testclient import TestClient

        app = main.app
        assert (app.title, app.description, app.version) == (
            "Xhuma", "A stateless middleware service for GP Connect to CCDA conversion", VERSION
        )
        import inspect
        lifespan = app.router.lifespan_context
        # Router inclusion wraps the configured lifespan without replacing it.
        while lifespan.__name__ == "merged_lifespan":
            lifespan = inspect.getclosurevars(inspect.unwrap(lifespan)).nonlocals["original_context"]
        assert lifespan.__name__ == "lifespan"
        assert inspect.isasyncgenfunction(inspect.unwrap(lifespan))
        assert {kind.__name__: handler.__name__ for kind, handler in app.exception_handlers.items()} == {
            "HTTPException": "http_exception_handler",
            "RequestValidationError": "request_validation_exception_handler",
            "WebSocketRequestValidationError": "websocket_request_validation_exception_handler",
            "SoapFault": "soap_fault_handler",
            "SoapError": "soap_error_handler",
            "AuditFailureException": "audit_failure_handler",
            "Exception": "global_exception_handler",
        }

        def middleware(app, hosts, origins):
            assert [(m.cls.__name__, m.args, m.kwargs) for m in app.user_middleware] == [
                ("MTLSMiddleware", (), {}),
                ("CORSMiddleware", (), dict(allow_origins=origins, allow_credentials=True,
                                            allow_methods=["*"], allow_headers=["*"])),
                ("TrustedHostMiddleware", (), dict(allowed_hosts=hosts)),
            ]

        middleware(app, ["*"], ["*"])
        # No context manager: these requests deliberately do not start external resources.
        client = TestClient(app)
        root = client.get("/")
        assert root.status_code == 200
        assert root.headers["content-type"] == "text/html; charset=utf-8"
        assert "<title>Welcome to Xhuma</title>" in root.text
        assert 'href="/docs#/"' in root.text
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json() == {"status": "ok"}
        assert health.headers["content-type"] == "application/json"
        assert "/" not in app.openapi()["paths"]
        client.close()

        os.environ["ALLOWED_HOSTS"] = " example.test, localhost "
        os.environ["CORS_ORIGINS"] = " https://one.test, https://two.test "
        importlib.reload(main)
        middleware(main.app, ["example.test", "localhost"], ["https://one.test", "https://two.test"])
        """,
    )


def test_registered_exception_contracts(tmp_path):
    run_isolated(
        tmp_path,
        """
        import asyncio
        import json
        import re
        from xml.etree import ElementTree as ET
        import app.main as main
        from app.audit.audit import AuditFailureException
        from fastapi import Request

        async def check():
            for kind, status in ((Exception, 500), (AuditFailureException, 502)):
                handler = main.app.exception_handlers[kind]
                for path, family in (("/SOAP/error", "soap"), ("/iti/error", "soap"),
                                     ("/other/SOAP/error", "soap"), ("/FHIR/error", "fhir"),
                                     ("/pds/error", "fhir"), ("/other/FHIR/error", "fhir"),
                                     ("/generic/error", "generic")):
                    request = Request(dict(type="http", path=path, headers=[], scheme="http",
                                           server=("testserver", 80), query_string=b"", app=main.app))
                    response = await handler(request, kind("PRIVATE_EXCEPTION_DETAIL"))
                    body = response.body.decode()
                    assert "PRIVATE_EXCEPTION_DETAIL" not in body
                    assert re.search(r"TraceID: [0-9a-f-]{36}", body)
                    if family == "soap":
                        assert response.status_code == 500
                        assert response.headers["content-type"].startswith("application/soap+xml")
                        xml = ET.fromstring(body)
                        ns = {"env": "http://www.w3.org/2003/05/soap-envelope"}
                        assert xml.findtext("env:Body/env:Fault/env:Code/env:Value", namespaces=ns) == "env:Receiver"
                        assert "Internal Server Error" in body
                    else:
                        assert response.status_code == status
                        assert response.headers["content-type"] == "application/json"
                        data = json.loads(body)
                        if family == "fhir":
                            assert data == {"resourceType": "OperationOutcome", "issue": [{
                                "severity": "fatal", "code": "exception",
                                "diagnostics": data["issue"][0]["diagnostics"],
                            }]}
                            assert data["issue"][0]["diagnostics"].startswith("An internal error occurred. TraceID: ")
                        else:
                            assert data == {"type": "about:blank", "title": "Internal Server Error",
                                            "status": status, "detail": data["detail"]}
                            assert data["detail"].startswith("An unexpected error occurred. TraceID: ")
        asyncio.run(check())
        """,
    )


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("entrypoint", ["main", "factory"])
def test_azure_instrumentation_first_import(tmp_path, enabled, entrypoint):
    run_isolated(
        tmp_path,
        f"""
        import types
        events = []
        def configure():
            assert "app.soap.soap" not in sys.modules
            events.append("configure")

        azure = types.ModuleType("azure.monitor.opentelemetry")
        azure.configure_azure_monitor = configure
        sys.modules[azure.__name__] = azure

        class FastAPIInstrumentor:
            @staticmethod
            def instrument_app(app):
                assert app.title == "Xhuma"
                events.append("fastapi")

        def instrumentor(name):
            class Instrumentor:
                def instrument(self, **kwargs):
                    assert kwargs == ({{"set_logging_format": True}} if name == "logging" else {{}})
                    events.append(name)
            return Instrumentor

        for name, cls in (("fastapi", FastAPIInstrumentor), ("httpx", instrumentor("httpx")),
                          ("logging", instrumentor("logging")), ("redis", instrumentor("redis"))):
            module = types.ModuleType("opentelemetry.instrumentation." + name)
            class_name = {{"fastapi": "FastAPIInstrumentor", "httpx": "HTTPXClientInstrumentor",
                          "logging": "LoggingInstrumentor", "redis": "RedisInstrumentor"}}[name]
            setattr(module, class_name, cls)
            sys.modules[module.__name__] = module

        if {enabled!r}:
            os.environ["APPLICATIONINSIGHTS_CONNECTION_STRING"] = "stub-only"
        if {entrypoint!r} == "main":
            import app.main as main
            assert importlib.import_module("app.main") is main
        else:
            from app.bootstrap.application import create_app
            assert events == []  # Importing the factory does not initialize instrumentation.
            app = create_app()
        expected = ["configure", "fastapi", "httpx", "logging", "redis"] if {enabled!r} else []
        assert events == expected  # Neither entrypoint may instrument twice.
        """,
    )
