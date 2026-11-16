import json
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from httpx import Response

from app.audit.models import AuditOutcome
from app.tests.gpconnect_fixtures import observed_pipeline as observed_pipeline
from app.tests.gpconnect_fixtures import observed_relay as observed_relay

pytest_plugins = ("pytest_asyncio",)
pytestmark = pytest.mark.usefixtures("mock_self_issuer")


@pytest.mark.asyncio
async def test_transport_direct_configuration(observed_pipeline):
    p = observed_pipeline
    response = await p.gp.gpconnect(p.nhs, p.saml, request=p.request)
    assert response.status_code == 200
    assert p.sent.url == "https://proxy.example.invalid/fake-gpconnect-endpoint/Patient/$gpc.getstructuredrecord"
    p.transport.create_nhs_ssl_context.assert_called_once_with("synthetic-cert", "synthetic-key", "synthetic-ca")
    kwargs = p.transport.httpx.AsyncClient.call_args.kwargs
    assert kwargs == {
        "cert": ("synthetic-cert", "synthetic-key"),
        "verify": p.transport.create_nhs_ssl_context.return_value,
        "timeout": httpx.Timeout(30.0),
        "http2": False,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "boundary,step,error",
    [
        ("entry", "http_client", RuntimeError("synthetic entry")),
        ("exit", "http_client_close", RuntimeError("synthetic exit")),
        ("post", "http_post", httpx.ReadError("synthetic read")),
        ("paths", "certificate_paths", FileNotFoundError("synthetic paths")),
        ("tls", "tls_context", FileNotFoundError("synthetic tls")),
    ],
)
async def test_transport_direct_failure_steps(observed_pipeline, monkeypatch, boundary, step, error):
    p = observed_pipeline
    client = p.transport.httpx.AsyncClient.return_value
    if boundary == "entry":
        client.__aenter__.side_effect = error
    elif boundary == "exit":
        client.__aexit__.side_effect = error
    elif boundary == "post":
        client.post.side_effect = error
    else:
        name = "get_nhs_cert_paths" if boundary == "paths" else "create_nhs_ssl_context"
        monkeypatch.setattr(p.transport, name, MagicMock(side_effect=error))
    response = await p.gp.gpconnect(p.nhs, p.saml, request=p.request)
    assert response.status_code == 502
    expected = (
        "ReadError: server closed connection before responding"
        if boundary == "post"
        else f"Transport error (step: gpconnect_request.{step}, exception_type: {type(error).__name__})"
    )
    assert json.loads(response.body) == {"success": False, "error": expected}
    assert p.audits[-1]["action"] == "gpconnect_request"
    assert p.audits[-1]["outcome"] == AuditOutcome.fail
    assert p.audits[-1]["detail"] == {
        "step": f"gpconnect_request.{step}",
        "transport": "direct",
        "exception_type": type(error).__name__,
    }
    assert "fhir.models" not in p.events
    assert not p.published


@pytest.mark.asyncio
@pytest.mark.parametrize("relay", [False, True])
async def test_transport_production_missing_base(observed_pipeline, monkeypatch, relay):
    p = observed_pipeline
    monkeypatch.setattr(p.gp, "environment", "prod")
    monkeypatch.setattr(p.gp, "USE_RELAY", relay)
    monkeypatch.setattr(p.gp, "RELAY_BASE_PATH" if relay else "OVER_INTERNET_PATH", None)
    response = await p.gp.gpconnect(p.nhs, p.saml, request=p.request)
    assert response.status_code == 502
    assert json.loads(response.body)["error"] == (
        "Transport error (step: gpconnect_request.transport_configuration, exception_type: ValueError)"
    )
    assert p.audits[-1]["detail"]["transport"] == ("relay" if relay else "direct")
    assert "xhuma.gpconnect.http" not in [s.name for s in p.exporter.get_finished_spans()]
    p.transport.httpx.AsyncClient.assert_not_called()


def test_transport_certificate_paths(monkeypatch, tmp_path):
    from app.gpconnect import get_nhs_cert_paths

    monkeypatch.setenv("NHS_CERT_DIR_EPHEMERAL", str(tmp_path))
    monkeypatch.setenv("ENV", "prod")
    with pytest.raises(FileNotFoundError, match="^Production NHS certificates not found in ephemeral storage$"):
        get_nhs_cert_paths()
    monkeypatch.setenv("ENV", "int")
    assert get_nhs_cert_paths() == tuple(
        f"keys/nhs_certs/{name}" for name in ("client_cert.pem", "client_key.pem", "nhs_bundle.pem")
    )
    # Only the certificate is checked here: missing key/CA are deferred to TLS setup.
    (tmp_path / "client_cert.pem").touch()
    monkeypatch.setenv("ENV", "prod")
    assert get_nhs_cert_paths() == tuple(
        str(tmp_path / name) for name in ("client_cert.pem", "client_key.pem", "nhs_bundle.pem")
    )


def test_transport_tls_configuration(monkeypatch, tmp_path):
    import ssl

    from app.gpconnect import create_nhs_ssl_context

    paths = [str(tmp_path / name) for name in ("cert", "key", "ca")]
    factory = MagicMock()
    monkeypatch.setattr(ssl, "create_default_context", factory)
    for path in paths:
        with pytest.raises(FileNotFoundError, match=f"^NHS Certificate file not found: {path}$"):
            create_nhs_ssl_context(*paths)
        factory.assert_not_called()
        Path(path).touch()
    context = create_nhs_ssl_context(*paths)
    factory.assert_called_once_with(ssl.Purpose.SERVER_AUTH)
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2
    context.load_cert_chain.assert_called_once_with(*paths[:2])
    context.load_verify_locations.assert_called_once_with(paths[2])
    error = ssl.SSLError("synthetic invalid certificate")
    context.load_cert_chain.side_effect = error
    with pytest.raises(ssl.SSLError) as caught:
        create_nhs_ssl_context(*paths)
    assert caught.value is error


@pytest.mark.asyncio
@pytest.mark.parametrize("external,token", [(False, False), (True, False), (True, True)])
async def test_transport_relay_success(observed_relay, monkeypatch, external, token):
    p = observed_relay
    if external:
        monkeypatch.setattr(p.settings, "EXTERNAL_RELAY_URL", "https://external.example.invalid///")
        monkeypatch.setattr(p.settings, "EXTERNAL_RELAY_TOKEN", "synthetic-token" if token else "")
        p.transport.httpx.AsyncClient.return_value.post.side_effect = None
        p.transport.httpx.AsyncClient.return_value.post.return_value = Response(200, json=p.relay_payload)
    response = await p.gp.gpconnect(p.nhs, p.saml, request=p.request)
    assert response.status_code == 200
    if external:
        p.transport.httpx.AsyncClient.assert_called_once_with(timeout=httpx.Timeout(75.0))
        call = p.transport.httpx.AsyncClient.return_value.post.call_args
        assert call.args == ("https://external.example.invalid/send/synthetic-client",)
        assert call.kwargs["headers"] == ({"Authorization": "Bearer synthetic-token"} if token else {})
        envelope = call.kwargs["json"]
        p.state.relay_hub.send.assert_not_awaited()
    else:
        p.transport.httpx.AsyncClient.assert_not_called()
        envelope = p.state.relay_hub.send.call_args.args[0]
    assert set(envelope) == {"request_id", "method", "url", "headers", "body"}
    from uuid import UUID

    assert str(UUID(envelope["request_id"])) == envelope["request_id"]
    # Preserve literal slash concatenation, including the double slash.
    url = "https://relay.example.invalid//fake-gpconnect-endpoint/Patient/$gpc.getstructuredrecord"
    assert envelope["url"] == url
    assert envelope["method"] == "POST"
    assert json.loads(envelope["body"])["parameter"][0]["valueIdentifier"]["value"] == p.nhs
    assert envelope["headers"]["Ssp-To"] == "200000000000"
    assert p.rebuilt[-1].request.method == "POST"
    assert str(p.rebuilt[-1].request.url) == url
    assert p.rebuilt[-1].json() == p.relay_payload["body"]
    assert "content-encoding" not in p.rebuilt[-1].headers
    assert "x-upstream" not in p.rebuilt[-1].headers
    p.transport.create_nhs_ssl_context.assert_not_called()
    spans = [s for s in p.exporter.get_finished_spans() if s.name == "xhuma.gpconnect.http"]
    assert len(spans) == 1
    assert spans[0].attributes["transport"] == "relay"
    assert spans[0].attributes["stage.outcome"] == "ok"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,expected",
    [
        ({"synthetic": True}, '{"synthetic": true}'),
        ([1, "x"], '[1, "x"]'),
        (b"bad\xff", "bad\ufffd"),
        ("text", "text"),
        (None, ""),
        (False, ""),
        (0, ""),
        (42, "42"),
    ],
)
async def test_transport_relay_body_conversion(observed_relay, body, expected):
    p = observed_relay
    p.relay_payload = {"status_code": "418", "body": body, "headers": {"content-encoding": "gzip"}}
    response = await p.gp.gpconnect(p.nhs, p.saml, request=p.request)
    assert response.status_code == 418
    assert json.loads(response.body)["error"] == "Error from GP Connect endpoint 418"
    assert p.rebuilt[-1].text == expected
    assert p.rebuilt[-1].status_code == 418
    assert "content-encoding" not in p.rebuilt[-1].headers
    assert p.audits[-1]["detail"]["step"] == "gpconnect_request.response_status"


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{"body": "missing status"}, {"status_code": 503, "body": "failure"}])
async def test_transport_relay_status_default(observed_relay, payload):
    p = observed_relay
    p.relay_payload = payload
    response = await p.gp.gpconnect(p.nhs, p.saml, request=p.request)
    assert response.status_code == payload.get("status_code", 502)
    assert p.rebuilt[-1].status_code == response.status_code
    assert p.audits[-1]["detail"]["step"] == "gpconnect_request.response_status"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case,step,status,error_type,detail",
    [
        ("missing-hub", "relay_hub_lookup", 404, "HTTPException", "Relay not available in this process"),
        ("hub-failure", "relay_hub_send", 502, "RuntimeError", None),
        ("hub-timeout", "relay_hub_send", 502, "TimeoutError", None),
        ("status", "relay_status", 502, "ValueError", None),
        ("external-failure", "external_relay_status", 502, "HTTPException", "External relay error"),
        ("external-decode", "external_relay_decode", 502, "JSONDecodeError", None),
        ("external-timeout", "external_relay_post", 502, "ReadTimeout", None),
        ("external-entry", "external_relay_client", 502, "RuntimeError", None),
        ("external-exit", "external_relay_client_close", 502, "RuntimeError", None),
    ],
)
async def test_transport_relay_failure_steps(observed_relay, monkeypatch, case, step, status, error_type, detail):
    p = observed_relay
    client = p.transport.httpx.AsyncClient.return_value
    if case.startswith("external"):
        monkeypatch.setattr(p.settings, "EXTERNAL_RELAY_URL", "https://external.example.invalid")
        client.post.side_effect = None
        client.post.return_value = Response(200, json=p.relay_payload)
        if case == "external-failure":
            client.post.return_value = Response(503)
        elif case == "external-decode":
            client.post.return_value = Response(200, text="not JSON")
        elif case == "external-timeout":
            client.post.side_effect = httpx.ReadTimeout("synthetic timeout")
        elif case == "external-entry":
            client.__aenter__.side_effect = RuntimeError("synthetic entry")
        elif case == "external-exit":
            client.__aexit__.side_effect = RuntimeError("synthetic exit")
    elif case == "missing-hub":
        del p.state.relay_hub
    elif case == "hub-failure":
        p.state.relay_hub.send.side_effect = RuntimeError("synthetic failure")
    elif case == "hub-timeout":
        p.state.relay_hub.send.side_effect = TimeoutError("synthetic timeout")
    else:
        p.relay_payload["status_code"] = "invalid"
    response = await p.gp.gpconnect(p.nhs, p.saml, request=p.request)
    assert response.status_code == status
    expected = (
        f"Transport error: {detail}"
        if detail
        else f"Transport error (step: gpconnect_request.{step}, exception_type: {error_type})"
    )
    assert json.loads(response.body) == {"success": False, "error": expected}
    assert p.audits[-1]["action"] == "gpconnect_request"
    assert p.audits[-1]["outcome"] == AuditOutcome.fail
    assert p.audits[-1]["detail"] == {
        "step": f"gpconnect_request.{step}",
        "transport": "relay",
        "exception_type": error_type,
    }
    assert not p.published
    assert "fhir.models" not in p.events
