import datetime as dt
import base64

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.relay.hub import WebSocketHub
from app.relay.routes import router


@pytest.fixture
def relay_client() -> TestClient:
    app = FastAPI()
    app.state.relay_hub = WebSocketHub()
    app.include_router(router)
    return TestClient(app)


def _make_test_cert() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "GB"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Xhuma Test"),
            x509.NameAttribute(NameOID.COMMON_NAME, "relay-agent-test"),
        ]
    )

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1))
        .not_valid_after(dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(private_key=key, algorithm=hashes.SHA256())
    )

    der_b64 = base64.b64encode(cert.public_bytes(serialization.Encoding.DER)).decode(
        "ascii"
    )
    fingerprint = cert.fingerprint(hashes.SHA256()).hex()
    return der_b64, fingerprint


def test_relay_ws_rejects_without_cert_header(monkeypatch, relay_client):
    monkeypatch.setenv("RELAY_REQUIRE_MTLS", "true")
    monkeypatch.setenv("RELAY_CLIENT_CERT_HEADER", "X-Relay-ClientCert")
    monkeypatch.delenv("RELAY_MTLS_ALLOWED_CERT_SHA256", raising=False)

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with relay_client.websocket_connect("/relay/ws/agent-1") as ws:
            ws.receive_text()

    assert exc_info.value.code == 1008


def test_relay_ws_rejects_non_allowlisted_cert(monkeypatch, relay_client):
    der_b64, _ = _make_test_cert()

    monkeypatch.setenv("RELAY_REQUIRE_MTLS", "true")
    monkeypatch.setenv("RELAY_CLIENT_CERT_HEADER", "X-Relay-ClientCert")
    monkeypatch.setenv("RELAY_MTLS_ALLOWED_CERT_SHA256", "deadbeef")

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with relay_client.websocket_connect(
            "/relay/ws/agent-1", headers={"X-Relay-ClientCert": der_b64}
        ) as ws:
            ws.receive_text()

    assert exc_info.value.code == 1008


def test_relay_ws_accepts_allowlisted_cert(monkeypatch, relay_client):
    der_b64, fingerprint = _make_test_cert()

    monkeypatch.setenv("RELAY_REQUIRE_MTLS", "true")
    monkeypatch.setenv("RELAY_CLIENT_CERT_HEADER", "X-Relay-ClientCert")
    monkeypatch.setenv("RELAY_MTLS_ALLOWED_CERT_SHA256", fingerprint)

    with relay_client.websocket_connect(
        "/relay/ws/agent-1", headers={"X-Relay-ClientCert": der_b64}
    ) as ws:
        # Send a minimal valid relay response payload.
        ws.send_text('{"request_id":"test","status_code":200,"text":"ok"}')
        assert ws is not None
