"""Consumer identity, outbound request construction and upstream status/audit handling."""

import json
import logging
import os
from collections.abc import Callable
from uuid import uuid4

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from fhirclient.models.fhirabstractbase import FHIRValidationError
from fhirclient.models.operationoutcome import OperationOutcome
from opentelemetry.propagate import inject

from .. import logging as application_logging
from ..audit import audit
from ..audit.audit import AuditFailureException
from ..audit.models import AuditOutcome, SAMLAttributes
from ..pds.pds import SDSUpstreamError, lookup_self_issuer
from ..security import create_jwt
from ..telemetry import measure
from .transport import send_request
from .types import DiagnosticStep, OutboundRequest, ProviderRoute


async def prepare_request(
    route: ProviderRoute,
    nhsno: int,
    saml_attrs: SAMLAttributes,
    *,
    get_parameters: Callable[[], list[dict]],
    request: Request | None,
    log_dir: str | None,
    step: DiagnosticStep,
) -> OutboundRequest | JSONResponse:
    """Build the request, reading facade-owned parameters after identity lookup as before."""
    fhir_endpoint_url, asid = route.endpoint_url, route.asid
    # 7) Build request
    try:
        step.value = "sds_self_lookup.issuer_lookup"
        with measure("sds.self"):
            issuer = await lookup_self_issuer()
        step.value = "sds_self_lookup.jwt_creation"
        token = create_jwt(saml_attrs, audience=f"{fhir_endpoint_url}", issuer=issuer)
        step.value = "sds_self_lookup.success_audit"
        await audit.attempt_audit(
            request=request,
            nhs_number=str(nhsno),
            saml=saml_attrs,
            action="sds_self_lookup",
            outcome=AuditOutcome.ok,
            detail={"step": step.value},
        )
    except AuditFailureException:
        raise
    except Exception as e:
        status_code = e.status_code if isinstance(e, HTTPException) else 502
        detail = {"step": step.value, "exception_type": type(e).__name__}
        if isinstance(e, SDSUpstreamError):
            status_code = e.http_status_code
            detail.update(reason=e.reason, upstream_status=e.status_code, attempts=e.attempts)
        diagnostic = f"SDS consumer issuer lookup failed (step: {step.value}, exception_type: {type(e).__name__})"
        if isinstance(e, SDSUpstreamError):
            diagnostic += (
                f" (reason={e.reason}, status={e.status_code}, attempts={e.attempts}, request_id={e.request_id})"
            )
        application_logging.record_application_failure(RuntimeError(diagnostic))
        logging.error(diagnostic)
        if log_dir:
            with open(os.path.join(log_dir, "error.log"), "a") as f:
                f.write(diagnostic + "\n")
        await audit.attempt_audit(
            request=request,
            nhs_number=str(nhsno),
            saml=saml_attrs,
            action="sds_self_lookup",
            outcome=AuditOutcome.fail,
            error_code=str(status_code),
            detail=detail,
        )
        msg = (
            "SDS consumer issuer lookup failed" + f": {e.detail}"
            if isinstance(e, (HTTPException, SDSUpstreamError))
            else diagnostic
        )
        return JSONResponse(status_code=status_code, content={"success": False, "error": msg})
    step.value = "gpconnect_request_build.headers"
    headers = {
        "Ssp-TraceID": str(uuid4()),
        "Ssp-From": os.environ["ORG_ASID"],
        "Ssp-To": asid,
        "Ssp-InteractionID": "urn:nhs:names:services:gpconnect:fhir:operation:gpc.getstructuredrecord-1",
        "Authorization": f"Bearer {token}",
        "accept": "application/fhir+json",
        "Content-Type": "application/fhir+json",
    }

    # Inject OpenTelemetry traceparent context into headers for Relay propagation
    step.value = "gpconnect_request_build.trace_injection"
    inject(headers)
    step.value = "gpconnect_request_build.request_body"
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "patientNHSNumber",
                "valueIdentifier": {
                    "system": "https://fhir.nhs.uk/Id/nhs-number",
                    "value": f"{nhsno}",
                },
            },
        ],
    }

    body["parameter"].extend(get_parameters())
    if log_dir:
        step.value = "gpconnect_request_build.request_debug_files"
        with open(os.path.join(log_dir, "request_headers.json"), "w") as f:
            json.dump(headers, f, indent=2)
        with open(os.path.join(log_dir, "request_body.json"), "w") as f:
            json.dump(body, f, indent=2)

    return OutboundRequest(headers=headers, body=body)


async def fetch_record(
    route: ProviderRoute,
    outbound: OutboundRequest,
    nhsno: int,
    saml_attrs: SAMLAttributes,
    *,
    relay_enabled: Callable[[], bool],
    environment: str,
    relay_base_path: str | None,
    over_internet_path: str | None,
    request: Request | None,
    log_dir: str | None,
    step: DiagnosticStep,
) -> httpx.Response | JSONResponse:
    """Retain transport error scopes and classify/audit the response outside its span.

    The relay flag is read again when recording failures, matching the facade's
    existing reads even if configuration is replaced while transport awaits.
    """
    fhir_endpoint_url = route.endpoint_url
    headers, body = outbound.headers, outbound.body
    # 8) Make request with a per-call client (avoid leaking across event loops)
    resp = None
    step.value = "gpconnect_request.transport_configuration"
    try:
        resp = await send_request(
            fhir_endpoint_url,
            headers,
            body,
            request=request,
            use_relay=relay_enabled(),
            environment=environment,
            relay_base_path=relay_base_path,
            over_internet_path=over_internet_path,
            step=step,
        )

        if log_dir:
            step.value = "gpconnect_request.response_debug_file"
            with open(os.path.join(log_dir, f"{resp.status_code}_response.json"), "w") as f:
                f.write(resp.text)
        logging.info(f"GP Connect request successful with status {resp.status_code}")

    except AuditFailureException:
        raise
    except httpx.ReadError as e:
        msg = "ReadError: server closed connection before responding"
        diagnostic = f"{msg} (step: {step.value}, exception_type: {type(e).__name__})"
        application_logging.record_application_failure(RuntimeError(diagnostic))
        logging.error(diagnostic)
        if log_dir:
            with open(os.path.join(log_dir, "error.log"), "a") as f:
                f.write(diagnostic + "\n")
        await audit.attempt_audit(
            request=request,
            request_id=headers.get("Ssp-TraceID"),
            nhs_number=str(nhsno),
            saml=saml_attrs,
            action="gpconnect_request",
            outcome=AuditOutcome.fail,
            error_code="502",
            detail={
                "step": step.value,
                "transport": "relay" if relay_enabled() else "direct",
                "exception_type": type(e).__name__,
            },
        )
        return JSONResponse(status_code=502, content={"success": False, "error": msg})

    except Exception as e:
        status_code = e.status_code if isinstance(e, HTTPException) else 502
        msg = "Transport error"
        diagnostic = f"{msg} (step: {step.value}, exception_type: {type(e).__name__})"
        application_logging.record_application_failure(RuntimeError(diagnostic))
        logging.error(diagnostic)
        if log_dir:
            with open(os.path.join(log_dir, "error.log"), "a") as f:
                f.write(diagnostic + "\n")
        await audit.attempt_audit(
            request=request,
            request_id=headers.get("Ssp-TraceID"),
            nhs_number=str(nhsno),
            saml=saml_attrs,
            action="gpconnect_request",
            outcome=AuditOutcome.fail,
            error_code=str(status_code),
            detail={
                "step": step.value,
                "transport": "relay" if relay_enabled() else "direct",
                "exception_type": type(e).__name__,
            },
        )
        msg = f"{msg}: {e.detail}" if isinstance(e, HTTPException) else diagnostic
        return JSONResponse(status_code=status_code, content={"success": False, "error": msg})

    # 9) Non-200 handling
    step.value = "gpconnect_request.response_status"
    if resp.status_code != 200:
        msg = f"Error from GP Connect endpoint {resp.status_code}"
        try:
            payload = resp.json()
            if isinstance(payload, dict) and payload.get("resourceType") == "OperationOutcome":
                outcome = OperationOutcome(payload)
                displays = [
                    " ".join(coding.display.split())
                    for issue in outcome.issue or []
                    if issue.details
                    for coding in issue.details.coding or []
                    if coding.display and coding.display.strip()
                ]
                if displays:
                    msg += ": " + " | ".join(dict.fromkeys(displays))
        except (ValueError, UnicodeDecodeError, FHIRValidationError):
            pass
        diagnostic = f"{msg} (step: {step.value})"
        application_logging.record_application_failure(RuntimeError(diagnostic))
        logging.error(diagnostic)
        if log_dir:
            with open(os.path.join(log_dir, "error.log"), "a") as f:
                f.write(diagnostic + "\n")
        await audit.attempt_audit(
            request=request,
            request_id=headers.get("Ssp-TraceID"),
            nhs_number=str(nhsno),
            saml=saml_attrs,
            action="gpconnect_request",
            outcome=AuditOutcome.fail,
            error_code=str(resp.status_code),
            detail={"step": step.value, "transport": "relay" if relay_enabled() else "direct"},
        )
        return JSONResponse(status_code=resp.status_code, content={"success": False, "error": msg})

    # audit successful response
    step.value = "gpconnect_request.success_audit"
    await audit.attempt_audit(
        request=request,
        request_id=headers.get("Ssp-TraceID"),
        nhs_number=str(nhsno),
        saml=saml_attrs,
        action="gpconnect_request",
        outcome=AuditOutcome.ok,
        detail={"step": step.value},
    )

    return resp
