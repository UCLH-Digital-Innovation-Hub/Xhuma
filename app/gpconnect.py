import json
import logging
import os
import time
from datetime import timedelta
from uuid import uuid4

import httpx
import xmltodict
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from fhirclient.models import bundle
from fhirclient.models.fhirabstractbase import FHIRValidationError
from fhirclient.models.operationoutcome import OperationOutcome
from opentelemetry.propagate import inject

from .audit.audit import AuditFailureException, attempt_audit
from .audit.models import AuditOutcome, SAMLAttributes
from .ccda.convert_mime import base64_xml
from .ccda.fhir2ccda import convert_bundle
from .ccda.helpers import validateNHSnumber
from .gp_connect_config import GP_CONNECT_PARAMETERS
from .gpconnect_stages.transport import DiagnosticStep, send_request
from .gpconnect_stages.transport import create_nhs_ssl_context as create_nhs_ssl_context
from .gpconnect_stages.transport import get_nhs_cert_paths as get_nhs_cert_paths
from .logging import record_application_failure
from .pds.pds import SDSUpstreamError, lookup_patient, lookup_self_issuer, sds_trace
from .redis_connect import redis_client
from .security import create_jwt
from .settings import USE_RELAY
from .telemetry import measure

router = APIRouter()

environment = os.getenv("ENV", "dev").lower()
if environment in ["dev", "int"]:
    RELAY_BASE_PATH = "https://proxy.int.spine2.ncrs.nhs.uk"
    IS_DEV = environment == "dev"
    OVER_INTERNET_PATH = "https://proxy.intspineservices.nhs.uk"
elif environment == "prod":
    RELAY_BASE_PATH = os.getenv("NHS_RELAY_BASE_PATH")
    OVER_INTERNET_PATH = os.getenv("NHS_OVER_INTERNET_PATH")
    IS_DEV = False
else:
    raise ValueError(f"Unknown or unsupported environment: {environment}")


logging.basicConfig(level=logging.INFO)
httpx_logger = logging.getLogger("httpx")
httpx_logger.setLevel(logging.WARNING)


@router.get("/gpconnect/{nhsno}")
async def gpconnect(nhsno: int, saml_attrs: SAMLAttributes, request: Request = None) -> JSONResponse:
    """accesses gp connect endpoint for nhs number"""
    return await _fetch_gpconnect_record(nhsno, saml_attrs, log_dir=None, request=request)


async def _fetch_gpconnect_record(
    nhsno: int, saml_attrs: SAMLAttributes, log_dir: str = None, request: Request = None
) -> JSONResponse:
    step = "validate_nhs_number.validate"
    try:
        # 1) Validate NHS number
        if validateNHSnumber(nhsno) is False:
            msg = f"{nhsno} is not a valid NHS number"
            await attempt_audit(
                request=request,
                nhs_number=str(nhsno),
                saml=saml_attrs,
                action="validate_nhs_number",
                outcome=AuditOutcome.fail,
                error_code="400",
                detail={"step": step},
            )
            return JSONResponse(status_code=400, content={"success": False, "error": msg})

        # 2) PDS lookup
        try:
            step = "pds_lookup.lookup_patient"
            with measure("pds.lookup"):
                pds_search = await lookup_patient(nhsno, request=request, saml=saml_attrs)
        except AuditFailureException:
            raise
        except HTTPException as e:
            diagnostic = f"PDS lookup failed (HTTP {e.status_code}) (step: {step}, exception_type: {type(e).__name__})"
            if e.status_code != 404:
                record_application_failure(RuntimeError(diagnostic))
            logging.error(diagnostic)
            if log_dir:
                with open(os.path.join(log_dir, "error.log"), "a") as f:
                    f.write(diagnostic + "\n")
            if e.status_code == 404:
                return JSONResponse(
                    status_code=404,
                    content={
                        "success": False,
                        "error": e.detail,
                        "xds_error_code": "XDSUnknownPatientId",
                    },
                )
            return JSONResponse(
                status_code=e.status_code, content={"success": False, "error": f"PDS lookup failed: {e.detail}"}
            )
        except Exception as e:
            status_code = e.status_code if isinstance(e, HTTPException) else 502
            msg = "PDS lookup failed"
            diagnostic = f"{msg} (step: {step}, exception_type: {type(e).__name__})"
            record_application_failure(RuntimeError(diagnostic))
            logging.error(diagnostic)
            if log_dir:
                with open(os.path.join(log_dir, "error.log"), "a") as f:
                    f.write(diagnostic + "\n")
            msg = f"{msg}: {e.detail}" if isinstance(e, HTTPException) else diagnostic
            return JSONResponse(status_code=status_code, content={"success": False, "error": msg})

        # 3) Security/U flag
        step = "check_restriction.security_extraction"
        try:
            security_code = pds_search["meta"]["security"][0]["code"]
        except Exception as e:
            diagnostic = f"Unable to read PDS security metadata (step: {step}, exception_type: {type(e).__name__})"
            record_application_failure(RuntimeError(diagnostic))
            logging.error(diagnostic)
            if log_dir:
                with open(os.path.join(log_dir, "error.log"), "a") as f:
                    f.write(diagnostic + "\n")
            security_code = None

        if security_code != "U":
            step = "check_restriction.restriction_check"
            msg = "Patient is not unrestricted, access to GP Connect is not permitted"
            await attempt_audit(
                request=request,
                nhs_number=str(nhsno),
                saml=saml_attrs,
                action="check_restriction",
                outcome=AuditOutcome.deny,
                error_code="403",
                detail={"step": step},
            )
            diagnostic = f"Patient is restricted (step: {step})"
            logging.error(diagnostic)
            if log_dir:
                with open(os.path.join(log_dir, "error.log"), "a") as f:
                    f.write(diagnostic + "\n")
            return JSONResponse(status_code=403, content={"success": False, "error": msg})

        # 4) Resolve GP ODS
        step = "resolve_gp_ods.ods_extraction"
        try:
            gp_ods = pds_search["generalPractitioner"][0]["identifier"]["value"]
        except AuditFailureException:
            raise
        except Exception as e:
            status_code = e.status_code if isinstance(e, HTTPException) else 502
            msg = "Unable to read GP ODS from PDS response"
            diagnostic = f"{msg} (step: {step}, exception_type: {type(e).__name__})"
            record_application_failure(RuntimeError(diagnostic))
            logging.error(diagnostic)
            if log_dir:
                with open(os.path.join(log_dir, "error.log"), "a") as f:
                    f.write(diagnostic + "\n")
            await attempt_audit(
                request=request,
                nhs_number=str(nhsno),
                saml=saml_attrs,
                action="resolve_gp_ods",
                outcome=AuditOutcome.fail,
                error_code=str(status_code),
                detail={"step": step, "exception_type": type(e).__name__},
            )
            msg = f"{msg}: {e.detail}" if isinstance(e, HTTPException) else diagnostic
            return JSONResponse(status_code=status_code, content={"success": False, "error": msg})

        # 5) Resolve provider Endpoint and MHS Party Key
        fhir_endpoint_url = None
        nhsmhsparty = None
        party_keys = []
        step = "sds_endpoint.sds_lookup"
        try:
            with measure("sds.endpoint"):
                endpoint_trace = await sds_trace(gp_ods, endpoint=True)

            if (
                isinstance(endpoint_trace, dict)
                and endpoint_trace.get("resourceType") == "Bundle"
                and endpoint_trace.get("entry") == []
            ):
                step = "sds_endpoint.entry_validation"
                msg = f"Unable to resolve GP Connect Structured Record FHIR endpoint in SDS for ODS code {gp_ods}"
                diagnostic = f"{msg} (step: {step})"
                logging.info(diagnostic)
                await attempt_audit(
                    request=request,
                    nhs_number=str(nhsno),
                    saml=saml_attrs,
                    action="fhir_endpoint_trace",
                    outcome=AuditOutcome.fail,
                    error_code="502",
                    detail={"step": step, "reason": "no_gp_connect_structured_record_route"},
                )
                return JSONResponse(status_code=502, content={"success": False, "error": msg})

            step = "sds_endpoint.response_validation"
            if (
                not isinstance(endpoint_trace, dict)
                or endpoint_trace.get("resourceType") != "Bundle"
                or not isinstance(endpoint_trace.get("entry"), list)
                or not endpoint_trace.get("entry")
            ):
                raise HTTPException(
                    status_code=502,
                    detail=f"SDS Endpoint lookup returned no entries for ODS code {gp_ods}",
                )

            entries = endpoint_trace["entry"]
            if len(entries) > 1:
                step = "sds_endpoint.entry_validation"
                msg = f"Ambiguous SDS Endpoint response for ODS code {gp_ods}"
                diagnostic = f"{msg} (step: {step})"
                record_application_failure(RuntimeError(diagnostic))
                logging.error(diagnostic)
                if log_dir:
                    with open(os.path.join(log_dir, "error.log"), "a") as f:
                        f.write(diagnostic + "\n")
                await attempt_audit(
                    request=request,
                    nhs_number=str(nhsno),
                    saml=saml_attrs,
                    action="fhir_endpoint_trace",
                    outcome=AuditOutcome.fail,
                    error_code="502",
                    detail={"step": step, "reason": "ambiguous_endpoint_entries"},
                )
                return JSONResponse(status_code=502, content={"success": False, "error": msg})

            step = "sds_endpoint.address_extraction"
            entry = entries[0]
            endpoint_resource = entry["resource"]
            if (
                isinstance(endpoint_resource, dict)
                and endpoint_resource.get("resourceType")
                and endpoint_resource.get("resourceType") != "Endpoint"
            ):
                raise ValueError(f"Unable to find FHIR endpoint for ODS code {gp_ods}")
            fhir_endpoint_url = endpoint_resource["address"]
            step = "sds_endpoint.address_validation"
            if not isinstance(fhir_endpoint_url, str) or not fhir_endpoint_url.strip():
                raise ValueError(f"Unable to find FHIR endpoint for ODS code {gp_ods}")

            step = "sds_endpoint.partykey_extraction"
            identifiers = endpoint_resource.get("identifier", [])
            party_keys = list(
                dict.fromkeys(
                    item.get("value")
                    for item in identifiers
                    if isinstance(item, dict)
                    and item.get("system") == "https://fhir.nhs.uk/Id/nhsMhsPartyKey"
                    and item.get("value")
                )
            )
            step = "sds_endpoint.partykey_validation"
            if not party_keys:
                raise ValueError(f"Unable to find nhsMhsPartyKey for ODS code {gp_ods}")
            if len(party_keys) > 1:
                raise ValueError(f"Ambiguous nhsMhsPartyKey for ODS code {gp_ods}")
            nhsmhsparty = party_keys[0]

            step = "sds_endpoint.success_audit"
            await attempt_audit(
                request=request,
                nhs_number=str(nhsno),
                saml=saml_attrs,
                action="fhir_endpoint_trace",
                outcome=AuditOutcome.ok,
                detail={"step": step},
            )
        except AuditFailureException:
            raise
        except Exception as e:
            status_code = e.status_code if isinstance(e, HTTPException) else 502
            detail = {"step": step, "exception_type": type(e).__name__}
            if isinstance(e, SDSUpstreamError):
                status_code = e.http_status_code
                detail.update(reason=e.reason, upstream_status=e.status_code, attempts=e.attempts)
            if step == "sds_endpoint.partykey_validation":
                if not party_keys:
                    detail["reason"] = "missing_nhs_mhs_party_key"
                elif len(party_keys) > 1:
                    detail["reason"] = "ambiguous_nhs_mhs_party_key"
            msg = f"SDS Endpoint resolution failed for ODS code {gp_ods}"
            diagnostic = f"{msg} (step: {step}, exception_type: {type(e).__name__})"
            if isinstance(e, SDSUpstreamError):
                diagnostic += (
                    f" (reason={e.reason}, status={e.status_code}, attempts={e.attempts}, request_id={e.request_id})"
                )
            record_application_failure(RuntimeError(diagnostic))
            logging.error(diagnostic)
            if log_dir:
                with open(os.path.join(log_dir, "error.log"), "a") as f:
                    f.write(diagnostic + "\n")
            await attempt_audit(
                request=request,
                nhs_number=str(nhsno),
                saml=saml_attrs,
                action="fhir_endpoint_trace",
                outcome=AuditOutcome.fail,
                error_code=str(status_code),
                detail=detail,
            )
            msg = f"{msg}: {e.detail}" if isinstance(e, (HTTPException, SDSUpstreamError)) else diagnostic
            return JSONResponse(status_code=status_code, content={"success": False, "error": msg})

        # 6) Resolve provider Device and ASID
        asid = None
        try:
            step = "sds_trace.sds_lookup"
            with measure("sds.trace"):
                asid_trace = await sds_trace(gp_ods, mhsparty=nhsmhsparty)
            step = "sds_trace.success_audit"
            await attempt_audit(
                request=request,
                nhs_number=str(nhsno),
                saml=saml_attrs,
                action="sds_trace",
                outcome=AuditOutcome.ok,
                detail={"step": step},
            )
        except AuditFailureException:
            raise
        except Exception as e:
            status_code = e.status_code if isinstance(e, HTTPException) else 502
            detail = {"step": step, "exception_type": type(e).__name__}
            if isinstance(e, SDSUpstreamError):
                status_code = e.http_status_code
                detail.update(reason=e.reason, upstream_status=e.status_code, attempts=e.attempts)
            msg = f"SDS Trace failed for ODS code {gp_ods}"
            diagnostic = f"{msg} (step: {step}, exception_type: {type(e).__name__})"
            if isinstance(e, SDSUpstreamError):
                diagnostic += (
                    f" (reason={e.reason}, status={e.status_code}, attempts={e.attempts}, request_id={e.request_id})"
                )
            record_application_failure(RuntimeError(diagnostic))
            logging.error(diagnostic)
            if log_dir:
                with open(os.path.join(log_dir, "error.log"), "a") as f:
                    f.write(diagnostic + "\n")
            await attempt_audit(
                request=request,
                nhs_number=str(nhsno),
                saml=saml_attrs,
                action="sds_trace",
                outcome=AuditOutcome.fail,
                error_code=str(status_code),
                detail=detail,
            )
            msg = f"{msg}: {e.detail}" if isinstance(e, (HTTPException, SDSUpstreamError)) else diagnostic
            return JSONResponse(status_code=status_code, content={"success": False, "error": msg})

        step = "resolve_asid_partykey.device_extraction"
        try:
            if not isinstance(asid_trace, dict) or asid_trace.get("resourceType") != "Bundle":
                raise ValueError(f"Expected a FHIR Bundle resource from SDS Device lookup for ODS code {gp_ods}")

            raw_entries = asid_trace.get("entry")
            if not isinstance(raw_entries, list):
                raise ValueError(f"Expected entry list in SDS Device lookup for ODS code {gp_ods}")

            device_entries = raw_entries
            if not device_entries:
                step = "resolve_asid_partykey.identity_validation"
                msg = f"Unable to find ASID for ODS code {gp_ods}"
                diagnostic = f"{msg} (step: {step})"
                record_application_failure(RuntimeError(diagnostic))
                logging.error(diagnostic)
                if log_dir:
                    with open(os.path.join(log_dir, "error.log"), "a") as f:
                        f.write(diagnostic + "\n")
                await attempt_audit(
                    request=request,
                    nhs_number=str(nhsno),
                    saml=saml_attrs,
                    action="resolve_asid_partykey",
                    outcome=AuditOutcome.fail,
                    error_code="500",
                    detail={"step": step, "message": msg},
                )
                return JSONResponse(status_code=500, content={"success": False, "error": msg})

            if len(device_entries) > 1:
                step = "resolve_asid_partykey.device_validation"
                msg = f"Ambiguous SDS Device response for ODS code {gp_ods}"
                diagnostic = f"{msg} (step: {step})"
                record_application_failure(RuntimeError(diagnostic))
                logging.error(diagnostic)
                if log_dir:
                    with open(os.path.join(log_dir, "error.log"), "a") as f:
                        f.write(diagnostic + "\n")
                await attempt_audit(
                    request=request,
                    nhs_number=str(nhsno),
                    saml=saml_attrs,
                    action="resolve_asid_partykey",
                    outcome=AuditOutcome.fail,
                    error_code="500",
                    detail={"step": step, "message": msg, "reason": "ambiguous_device_entries"},
                )
                return JSONResponse(status_code=500, content={"success": False, "error": msg})

            entry = device_entries[0]
            if not isinstance(entry, dict):
                raise TypeError(f"Invalid entry in SDS Device response for ODS code {gp_ods}")
            device_resource = entry.get("resource")
            if not isinstance(device_resource, dict):
                raise AttributeError(f"Invalid resource in SDS Device response for ODS code {gp_ods}")
            if device_resource.get("resourceType") and device_resource.get("resourceType") != "Device":
                raise ValueError(f"Invalid FHIR resourceType in SDS Device response for ODS code {gp_ods}")

            step = "resolve_asid_partykey.identifier_extraction"
            identifiers = device_resource.get("identifier")
            if identifiers is None:
                raise TypeError("Device identifier list is None")
            asids = list(
                dict.fromkeys(
                    item.get("value")
                    for item in identifiers
                    if isinstance(item, dict)
                    and item.get("system") == "https://fhir.nhs.uk/Id/nhsSpineASID"
                    and item.get("value")
                )
            )
            step = "resolve_asid_partykey.identity_validation"
            if not asids:
                msg = f"Unable to find ASID for ODS code {gp_ods}"
                diagnostic = f"{msg} (step: {step})"
                record_application_failure(RuntimeError(diagnostic))
                logging.error(diagnostic)
                if log_dir:
                    with open(os.path.join(log_dir, "error.log"), "a") as f:
                        f.write(diagnostic + "\n")
                await attempt_audit(
                    request=request,
                    nhs_number=str(nhsno),
                    saml=saml_attrs,
                    action="resolve_asid_partykey",
                    outcome=AuditOutcome.fail,
                    error_code="500",
                    detail={"step": step, "message": msg},
                )
                return JSONResponse(status_code=500, content={"success": False, "error": msg})
            if len(asids) > 1:
                msg = f"Ambiguous ASID for ODS code {gp_ods}"
                diagnostic = f"{msg} (step: {step})"
                record_application_failure(RuntimeError(diagnostic))
                logging.error(diagnostic)
                if log_dir:
                    with open(os.path.join(log_dir, "error.log"), "a") as f:
                        f.write(diagnostic + "\n")
                await attempt_audit(
                    request=request,
                    nhs_number=str(nhsno),
                    saml=saml_attrs,
                    action="resolve_asid_partykey",
                    outcome=AuditOutcome.fail,
                    error_code="500",
                    detail={"step": step, "message": msg},
                )
                return JSONResponse(status_code=500, content={"success": False, "error": msg})
            asid = asids[0]
        except AuditFailureException:
            raise
        except Exception as e:
            status_code = e.status_code if isinstance(e, HTTPException) else 500
            msg = "Unable to parse SDS trace response"
            diagnostic = f"{msg} (step: {step}, exception_type: {type(e).__name__})"
            record_application_failure(RuntimeError(diagnostic))
            logging.error(diagnostic)
            if log_dir:
                with open(os.path.join(log_dir, "error.log"), "a") as f:
                    f.write(diagnostic + "\n")
            await attempt_audit(
                request=request,
                nhs_number=str(nhsno),
                saml=saml_attrs,
                action="resolve_asid_partykey",
                outcome=AuditOutcome.fail,
                error_code=str(status_code),
                detail={"step": step, "exception_type": type(e).__name__},
            )
            msg = f"{msg}: {e.detail}" if isinstance(e, HTTPException) else diagnostic
            return JSONResponse(status_code=status_code, content={"success": False, "error": msg})

        # 7) Build request
        try:
            step = "sds_self_lookup.issuer_lookup"
            with measure("sds.self"):
                issuer = await lookup_self_issuer()
            step = "sds_self_lookup.jwt_creation"
            token = create_jwt(saml_attrs, audience=f"{fhir_endpoint_url}", issuer=issuer)
            step = "sds_self_lookup.success_audit"
            await attempt_audit(
                request=request,
                nhs_number=str(nhsno),
                saml=saml_attrs,
                action="sds_self_lookup",
                outcome=AuditOutcome.ok,
                detail={"step": step},
            )
        except AuditFailureException:
            raise
        except Exception as e:
            status_code = e.status_code if isinstance(e, HTTPException) else 502
            detail = {"step": step, "exception_type": type(e).__name__}
            if isinstance(e, SDSUpstreamError):
                status_code = e.http_status_code
                detail.update(reason=e.reason, upstream_status=e.status_code, attempts=e.attempts)
            diagnostic = f"SDS consumer issuer lookup failed (step: {step}, exception_type: {type(e).__name__})"
            if isinstance(e, SDSUpstreamError):
                diagnostic += (
                    f" (reason={e.reason}, status={e.status_code}, attempts={e.attempts}, request_id={e.request_id})"
                )
            record_application_failure(RuntimeError(diagnostic))
            logging.error(diagnostic)
            if log_dir:
                with open(os.path.join(log_dir, "error.log"), "a") as f:
                    f.write(diagnostic + "\n")
            await attempt_audit(
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
        step = "gpconnect_request_build.headers"
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
        step = "gpconnect_request_build.trace_injection"
        inject(headers)
        step = "gpconnect_request_build.request_body"
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

        body["parameter"].extend(GP_CONNECT_PARAMETERS)
        if log_dir:
            step = "gpconnect_request_build.request_debug_files"
            with open(os.path.join(log_dir, "request_headers.json"), "w") as f:
                json.dump(headers, f, indent=2)
            with open(os.path.join(log_dir, "request_body.json"), "w") as f:
                json.dump(body, f, indent=2)

        # 8) Make request with a per-call client (avoid leaking across event loops)
        resp = None
        step = "gpconnect_request.transport_configuration"
        transport_step = DiagnosticStep(step)
        try:
            try:
                resp = await send_request(
                    fhir_endpoint_url,
                    headers,
                    body,
                    request=request,
                    use_relay=USE_RELAY,
                    environment=environment,
                    relay_base_path=RELAY_BASE_PATH,
                    over_internet_path=OVER_INTERNET_PATH,
                    step=transport_step,
                )
            finally:
                # Preserve the last transport operation for the existing exception handlers.
                step = transport_step.value

            if log_dir:
                step = "gpconnect_request.response_debug_file"
                with open(os.path.join(log_dir, f"{resp.status_code}_response.json"), "w") as f:
                    f.write(resp.text)
            logging.info(f"GP Connect request successful with status {resp.status_code}")

        except AuditFailureException:
            raise
        except httpx.ReadError as e:
            msg = "ReadError: server closed connection before responding"
            diagnostic = f"{msg} (step: {step}, exception_type: {type(e).__name__})"
            record_application_failure(RuntimeError(diagnostic))
            logging.error(diagnostic)
            if log_dir:
                with open(os.path.join(log_dir, "error.log"), "a") as f:
                    f.write(diagnostic + "\n")
            await attempt_audit(
                request=request,
                request_id=headers.get("Ssp-TraceID"),
                nhs_number=str(nhsno),
                saml=saml_attrs,
                action="gpconnect_request",
                outcome=AuditOutcome.fail,
                error_code="502",
                detail={
                    "step": step,
                    "transport": "relay" if USE_RELAY else "direct",
                    "exception_type": type(e).__name__,
                },
            )
            return JSONResponse(status_code=502, content={"success": False, "error": msg})

        except Exception as e:
            status_code = e.status_code if isinstance(e, HTTPException) else 502
            msg = "Transport error"
            diagnostic = f"{msg} (step: {step}, exception_type: {type(e).__name__})"
            record_application_failure(RuntimeError(diagnostic))
            logging.error(diagnostic)
            if log_dir:
                with open(os.path.join(log_dir, "error.log"), "a") as f:
                    f.write(diagnostic + "\n")
            await attempt_audit(
                request=request,
                request_id=headers.get("Ssp-TraceID"),
                nhs_number=str(nhsno),
                saml=saml_attrs,
                action="gpconnect_request",
                outcome=AuditOutcome.fail,
                error_code=str(status_code),
                detail={
                    "step": step,
                    "transport": "relay" if USE_RELAY else "direct",
                    "exception_type": type(e).__name__,
                },
            )
            msg = f"{msg}: {e.detail}" if isinstance(e, HTTPException) else diagnostic
            return JSONResponse(status_code=status_code, content={"success": False, "error": msg})

        # 9) Non-200 handling
        step = "gpconnect_request.response_status"
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
            diagnostic = f"{msg} (step: {step})"
            record_application_failure(RuntimeError(diagnostic))
            logging.error(diagnostic)
            if log_dir:
                with open(os.path.join(log_dir, "error.log"), "a") as f:
                    f.write(diagnostic + "\n")
            await attempt_audit(
                request=request,
                request_id=headers.get("Ssp-TraceID"),
                nhs_number=str(nhsno),
                saml=saml_attrs,
                action="gpconnect_request",
                outcome=AuditOutcome.fail,
                error_code=str(resp.status_code),
                detail={"step": step, "transport": "relay" if USE_RELAY else "direct"},
            )
            return JSONResponse(status_code=resp.status_code, content={"success": False, "error": msg})

        # audit successful response
        step = "gpconnect_request.success_audit"
        await attempt_audit(
            request=request,
            request_id=headers.get("Ssp-TraceID"),
            nhs_number=str(nhsno),
            saml=saml_attrs,
            action="gpconnect_request",
            outcome=AuditOutcome.ok,
            detail={"step": step},
        )

        # 10) Validate the entire upstream payload before conversion or caching.
        step = "validate_fhir_bundle.response_content"
        try:
            step = "validate_fhir_bundle.json_decode"
            with measure("fhir.decode", input_bytes=len(resp.content)):
                scr_bundle = json.loads(resp.text)
            step = "validate_fhir_bundle.resource_type"
            if not isinstance(scr_bundle, dict) or scr_bundle.get("resourceType") != "Bundle":
                raise ValueError("Expected a FHIR Bundle resource")
            # Retain the existing compatibility handling for standalone comment entries.
            step = "validate_fhir_bundle.comment_entries"
            for j, entry in enumerate(scr_bundle.get("entry", [])):
                if "fhir_comments" in entry:
                    scr_bundle["entry"].pop(j)
                    break
            step = "validate_fhir_bundle.fhir_models"
            with measure("fhir.models", resource_count=len(scr_bundle.get("entry", []))):
                fhir_bundle = bundle.Bundle(scr_bundle)
        except AuditFailureException:
            raise
        except Exception as e:
            status_code = e.status_code if isinstance(e, HTTPException) else 502
            msg = "FHIR bundle malformed"
            diagnostic = f"{msg} (step: {step}, exception_type: {type(e).__name__})"
            record_application_failure(RuntimeError(diagnostic))
            logging.error(diagnostic)
            if log_dir:
                with open(os.path.join(log_dir, "error.log"), "a") as f:
                    f.write(diagnostic + "\n")
            await attempt_audit(
                request=request,
                request_id=headers.get("Ssp-TraceID"),
                nhs_number=str(nhsno),
                saml=saml_attrs,
                action="validate_fhir_bundle",
                outcome=AuditOutcome.fail,
                error_code=str(status_code),
                detail={"step": step, "exception_type": type(e).__name__},
            )
            msg = "FHIR bundle malformed" + f": {e.detail}" if isinstance(e, HTTPException) else diagnostic
            return JSONResponse(status_code=status_code, content={"success": False, "error": msg})

        # index resources for resolution
        step = "fhir_index.resource_index"
        with measure("fhir.index", resource_count=len(fhir_bundle.entry or [])):
            bundle_index = {}
            for entry in fhir_bundle.entry or []:
                try:
                    addr = f"{entry.resource.resource_type}/{entry.resource.id}"
                    bundle_index[addr] = entry.resource
                    if entry.fullUrl:
                        bundle_index[entry.fullUrl] = entry.resource
                except Exception as e:
                    # Preserve compatibility with entries that cannot be indexed.
                    logging.warning(
                        "Skipping FHIR resource (step: fhir_index.resource_index, exception_type: %s)",
                        type(e).__name__,
                    )

        step = "ccda_convert.start_timer"
        start_time = time.perf_counter()
        try:
            step = "ccda_convert.convert_bundle"
            with measure(
                "ccda.convert", span_name="FHIR2CCDA.convert_bundle", resource_count=len(fhir_bundle.entry or [])
            ):
                xml_ccda = await convert_bundle(fhir_bundle, bundle_index)

            step = "ccda_convert.duration"
            duration_ms = (time.perf_counter() - start_time) * 1000
            logging.info(f"FHIR2CCDA conversion completed in {duration_ms:.2f}ms")
        except AuditFailureException:
            raise
        except Exception as e:
            status_code = e.status_code if isinstance(e, HTTPException) else 500
            msg = "Failed to convert FHIR Bundle to CCDA"
            diagnostic = f"{msg} (step: {step}, exception_type: {type(e).__name__})"
            record_application_failure(RuntimeError(diagnostic))
            logging.error(diagnostic)
            if log_dir:
                with open(os.path.join(log_dir, "error.log"), "a") as f:
                    f.write(diagnostic + "\n")
            msg = f"{msg}: {e.detail}" if isinstance(e, HTTPException) else diagnostic
            return JSONResponse(status_code=status_code, content={"success": False, "error": msg})

        if log_dir:
            step = "ccda_serialize.xml_debug_file"
            # CodeQL static analysis requires explicit integer casting to drop path traversal taint
            safe_nhsno = str(int(nhsno))
            with open(os.path.join(log_dir, f"{safe_nhsno}.xml"), "w") as output:
                output.write(xmltodict.unparse(xml_ccda, pretty=True))

        step = "ccda_serialize.base64_xml"
        with measure("ccda.serialize") as span:
            xop = base64_xml(xml_ccda)
            step = "ccda_serialize.document_size"
            span.set_attribute("document.base64_bytes", len(xop))
        step = "store_ccda.document_id"
        doc_uuid = str(uuid4())
        step = "store_ccda.success_audit"
        await attempt_audit(
            request=request,
            nhs_number=str(nhsno),
            saml=saml_attrs,
            action="store_ccda",
            outcome=AuditOutcome.ok,
            document_id=doc_uuid,
            detail={"step": step},
        )

        step = "ccda_cache.expiry_configuration"
        if request and hasattr(request, "app") and hasattr(request.app.state, "ccda_expiry_hours"):
            expiry_hours = request.app.state.ccda_expiry_hours
        else:
            expiry_hours = float(os.getenv("CCDA_EXPIRY_HOURS", "4"))

        step = "ccda_cache.cache_ttl"
        cache_ttl = timedelta(hours=expiry_hours)

        # Use a pipeline to write all keys atomically with a consistent TTL
        step = "ccda_cache.pipeline_open"
        async with redis_client.pipeline() as pipe:
            step = "ccda_cache.queue_patient_document"
            pipe.setex(str(nhsno), cache_ttl, doc_uuid)
            document_key = f"document:{doc_uuid}"
            step = "ccda_cache.queue_document_content"
            pipe.hset(document_key, mapping={"nhs_number": str(nhsno), "content": xop})
            step = "ccda_cache.queue_document_expiry"
            pipe.expire(document_key, cache_ttl)
            step = "ccda_cache.pipeline_execute"
            with measure("ccda.cache.write", document_bytes=len(xop)):
                await pipe.execute()
            step = "ccda_cache.pipeline_close"

        # only write the xml if dev
        if os.getenv("ENV", "prod").lower() in ("dev", "local"):
            step = "ccda_serialize.local_xml_file"
            with open(f"{int(nhsno)!s}.xml", "w") as output:
                output.write(xmltodict.unparse(xml_ccda, pretty=True))

        step = "gpconnect_response.json_response"
        return JSONResponse(status_code=200, content={"success": True, "document_id": doc_uuid})
    except Exception as e:
        diagnostic = f"GP Connect failed (step: {step}, exception_type: {type(e).__name__})"
        record_application_failure(RuntimeError(diagnostic))
        logging.error(diagnostic)
        if log_dir:
            with open(os.path.join(log_dir, "error.log"), "a") as f:
                f.write(diagnostic + "\n")
        if isinstance(e, AuditFailureException):
            raise AuditFailureException("GP Connect audit persistence failed") from None
        status_code = e.status_code if isinstance(e, HTTPException) else 500
        msg = f"GP Connect failed at {step}: {e.detail}" if isinstance(e, HTTPException) else diagnostic
        return JSONResponse(status_code=status_code, content={"success": False, "error": msg})
