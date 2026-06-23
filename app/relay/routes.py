import json
import os
import urllib.parse
import base64

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi import WebSocketException, status

router = APIRouter(prefix="/relay", tags=["relay"])


def _env_is_true(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() in {"1", "true", "yes", "on"}


def _parse_client_cert_from_header(value: str) -> x509.Certificate | None:
    # Header may arrive as URL-encoded PEM or base64-encoded DER.
    decoded = urllib.parse.unquote(value).strip()

    if "BEGIN CERTIFICATE" in decoded:
        try:
            return x509.load_pem_x509_certificate(decoded.encode("utf-8"))
        except ValueError:
            return None

    try:
        return x509.load_der_x509_certificate(base64.b64decode(decoded))
    except Exception:
        return None


def _allowed_cert_fingerprints() -> set[str]:
    raw = os.getenv("RELAY_MTLS_ALLOWED_CERT_SHA256", "")
    values = {v.strip().lower().replace(":", "") for v in raw.split(",") if v.strip()}
    return values


def _enforce_relay_mtls(websocket: WebSocket) -> None:
    if not _env_is_true("RELAY_REQUIRE_MTLS", "true"):
        return

    cert_header = os.getenv("RELAY_CLIENT_CERT_HEADER", "X-Relay-ClientCert")
    cert_value = websocket.headers.get(cert_header)

    if not cert_value:
        print(
            f"Relay Auth failed: Client certificate ({cert_header}) required",
            flush=True,
        )
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason=f"Client certificate ({cert_header}) required",
        )

    cert = _parse_client_cert_from_header(cert_value)
    if cert is None:
        print("Relay mTLS failed: Invalid client certificate format", flush=True)
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Invalid client certificate format",
        )

    allowed = _allowed_cert_fingerprints()
    if not allowed:
        return

    fingerprint = cert.fingerprint(hashes.SHA256()).hex()
    if fingerprint not in allowed:
        print(
            f"Relay mTLS failed: Relay client certificate {fingerprint} is not allow-listed",
            flush=True,
        )
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Relay client certificate is not allow-listed",
        )


@router.websocket("/ws/{client_id}")
async def relay_ws(websocket: WebSocket, client_id: str):
    await websocket.accept()
    try:
        _enforce_relay_mtls(websocket)
    except WebSocketException as e:
        await websocket.close(code=e.code, reason=e.reason)
        return

    hub = websocket.app.state.relay_hub
    await hub.register(websocket)
    try:
        while True:
            # Agent sends RelayResponse JSON
            data = await websocket.receive_text()
            hub.fulfill(json.loads(data))
    except WebSocketDisconnect:
        pass
    finally:
        await hub.unregister(websocket)
