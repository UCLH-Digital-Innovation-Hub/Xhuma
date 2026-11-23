"""GP Connect direct and relay transport; audit and HTTP classification stay with the caller."""

import json
import os
import ssl
from uuid import uuid4

import httpx
from fastapi import HTTPException, Request

from ..telemetry import measure
from .types import DiagnosticStep as DiagnosticStep


def get_nhs_cert_paths():
    cert_dir = os.environ.get("NHS_CERT_DIR_EPHEMERAL", "/tmp/nhs_certs")
    if os.path.exists(os.path.join(cert_dir, "client_cert.pem")):
        return (
            os.path.join(cert_dir, "client_cert.pem"),
            os.path.join(cert_dir, "client_key.pem"),
            os.path.join(cert_dir, "nhs_bundle.pem"),
        )

    environment = os.getenv("ENV", "dev").lower()
    if environment == "prod":
        # In production, we must fail closed and never fall back to repo keys
        raise FileNotFoundError("Production NHS certificates not found in ephemeral storage")

    return (
        "keys/nhs_certs/client_cert.pem",
        "keys/nhs_certs/client_key.pem",
        "keys/nhs_certs/nhs_bundle.pem",
    )


def create_nhs_ssl_context(cert_path, key_path, ca_path):
    # Verify files exist before trying to load
    for p in [cert_path, key_path, ca_path]:
        if not os.path.exists(p):
            raise FileNotFoundError(f"NHS Certificate file not found: {p}")

    ssl_context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    ssl_context.check_hostname = True
    ssl_context.verify_mode = ssl.CERT_REQUIRED
    ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
    ssl_context.load_cert_chain(cert_path, key_path)
    ssl_context.load_verify_locations(ca_path)
    return ssl_context


async def _direct_http_call(url: str, headers: dict, body: dict, step: DiagnosticStep) -> httpx.Response:
    """Make a direct POST and return an httpx.Response."""
    step.value = "gpconnect_request.certificate_paths"
    cert_path, key_path, ca_path = get_nhs_cert_paths()
    step.value = "gpconnect_request.tls_context"
    ssl_context = create_nhs_ssl_context(cert_path, key_path, ca_path)
    step.value = "gpconnect_request.http_client"
    async with httpx.AsyncClient(
        cert=(cert_path, key_path),
        verify=ssl_context,
        timeout=httpx.Timeout(30.0),
        http2=False,
    ) as session:
        step.value = "gpconnect_request.http_post"
        r = await session.post(url, json=body, headers=headers)
        step.value = "gpconnect_request.http_client_close"
        return r  # return a real httpx.Response


async def _relay_call(
    url: str, headers: dict, body: dict, request: Request | None, step: DiagnosticStep
) -> httpx.Response:
    """Send via relay and return an httpx.Response with status_code and text."""
    step.value = "gpconnect_request.relay_request"
    # Fix compatibility with standalone Relay Server RelayRequest schema
    relay_req = {
        "request_id": str(uuid4()),
        "method": "POST",
        "url": url,
        "headers": headers,
        "body": json.dumps(body) if isinstance(body, dict) else str(body),
    }

    step.value = "gpconnect_request.relay_configuration"
    from ..settings import (
        EXTERNAL_RELAY_CLIENT_ID,
        EXTERNAL_RELAY_TOKEN,
        EXTERNAL_RELAY_URL,
    )

    if EXTERNAL_RELAY_URL:
        req_headers = {}
        if EXTERNAL_RELAY_TOKEN:
            req_headers["Authorization"] = f"Bearer {EXTERNAL_RELAY_TOKEN}"

        step.value = "gpconnect_request.external_relay_client"
        async with httpx.AsyncClient(timeout=httpx.Timeout(75.0)) as client:
            relay_target = f"{EXTERNAL_RELAY_URL.rstrip('/')}/send/{EXTERNAL_RELAY_CLIENT_ID}"
            step.value = "gpconnect_request.external_relay_post"
            ext_resp = await client.post(relay_target, headers=req_headers, json=relay_req)
            step.value = "gpconnect_request.external_relay_status"
            if ext_resp.status_code != 200:
                raise HTTPException(502, "External relay error")
            step.value = "gpconnect_request.external_relay_decode"
            resp = ext_resp.json()
            step.value = "gpconnect_request.external_relay_client_close"
    else:
        step.value = "gpconnect_request.relay_hub_lookup"
        hub = getattr(request.app.state, "relay_hub", None)
        if not hub:
            raise HTTPException(404, "Relay not available in this process")
        step.value = "gpconnect_request.relay_hub_send"
        resp: dict = await hub.send(relay_req)

    step.value = "gpconnect_request.relay_status"
    status_code = int(resp.get("status_code", 502))
    step.value = "gpconnect_request.relay_body"
    body_raw = resp.get("body")

    # Make sure body is text
    if isinstance(body_raw, (dict, list)):
        body_text = json.dumps(body_raw)
    elif isinstance(body_raw, bytes):
        body_text = body_raw.decode("utf-8", errors="replace")
    else:
        body_text = str(body_raw or "")

    # Build a minimal httpx.Response — no header reuse (prevents gzip errors)
    step.value = "gpconnect_request.relay_response"
    return httpx.Response(
        status_code=status_code,
        content=body_text.encode("utf-8"),
        request=httpx.Request("POST", url),
    )


async def send_request(
    fhir_endpoint_url: str,
    headers: dict,
    body: dict,
    *,
    request: Request | None,
    use_relay: bool,
    environment: str,
    relay_base_path: str | None,
    over_internet_path: str | None,
    step: DiagnosticStep,
) -> httpx.Response:
    """Select the existing transport, preserving literal URLs and the transport span."""
    if use_relay:
        if environment == "prod" and not relay_base_path:
            raise ValueError("NHS_RELAY_BASE_PATH not configured for production. Failing closed.")
        url = f"{relay_base_path}/{fhir_endpoint_url}/Patient/$gpc.getstructuredrecord"
        step.value = "gpconnect_request.relay_call"
        with measure("gpconnect.http", transport="relay"):
            return await _relay_call(url, headers, body, request, step)

    else:
        if environment == "prod" and not over_internet_path:
            raise ValueError("NHS_OVER_INTERNET_PATH not configured for production. Failing closed.")
        url = f"{over_internet_path}/{fhir_endpoint_url}/Patient/$gpc.getstructuredrecord"
        step.value = "gpconnect_request.direct_call"
        with measure("gpconnect.http", transport="direct"):
            return await _direct_http_call(url, headers, body, step)
