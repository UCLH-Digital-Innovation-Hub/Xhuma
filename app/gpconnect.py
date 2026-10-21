import logging
import os

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fhirclient.models.fhirabstractbase import FHIRValidationError as FHIRValidationError

from . import logging as application_logging
from .audit.audit import AuditFailureException
from .audit.audit import attempt_audit as attempt_audit
from .audit.models import SAMLAttributes
from .gp_connect_config import GP_CONNECT_PARAMETERS
from .gpconnect_stages.document import process_document
from .gpconnect_stages.patient import resolve_patient
from .gpconnect_stages.request import fetch_record, prepare_request
from .gpconnect_stages.routing import resolve_provider
from .gpconnect_stages.transport import create_nhs_ssl_context as create_nhs_ssl_context
from .gpconnect_stages.transport import get_nhs_cert_paths as get_nhs_cert_paths
from .gpconnect_stages.types import DiagnosticStep
from .settings import USE_RELAY

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
        patient_step = DiagnosticStep(step)
        try:
            gp_ods = await resolve_patient(
                nhsno,
                saml_attrs,
                request=request,
                log_dir=log_dir,
                step=patient_step,
            )
        finally:
            step = patient_step.value
        if isinstance(gp_ods, Response):
            return gp_ods

        routing_step = DiagnosticStep(step)
        try:
            route = await resolve_provider(
                gp_ods,
                nhsno,
                saml_attrs,
                request=request,
                log_dir=log_dir,
                step=routing_step,
            )
        finally:
            step = routing_step.value
        if isinstance(route, Response):
            return route

        request_step = DiagnosticStep(step)
        try:
            outbound = await prepare_request(
                route,
                nhsno,
                saml_attrs,
                get_parameters=lambda: GP_CONNECT_PARAMETERS,
                request=request,
                log_dir=log_dir,
                step=request_step,
            )
            if isinstance(outbound, Response):
                return outbound
            resp = await fetch_record(
                route,
                outbound,
                nhsno,
                saml_attrs,
                relay_enabled=lambda: USE_RELAY,
                environment=environment,
                relay_base_path=RELAY_BASE_PATH,
                over_internet_path=OVER_INTERNET_PATH,
                request=request,
                log_dir=log_dir,
                step=request_step,
            )
        finally:
            step = request_step.value
        if isinstance(resp, Response):
            return resp

        document_step = DiagnosticStep(step)
        try:
            document_result = await process_document(
                resp,
                nhsno,
                saml_attrs,
                ssp_trace_id=outbound.headers.get("Ssp-TraceID"),
                request=request,
                log_dir=log_dir,
                step=document_step,
            )
        finally:
            step = document_step.value
        if isinstance(document_result, Response):
            return document_result
        doc_uuid = document_result

        step = "gpconnect_response.json_response"
        return JSONResponse(status_code=200, content={"success": True, "document_id": doc_uuid})
    except Exception as e:
        diagnostic = f"GP Connect failed (step: {step}, exception_type: {type(e).__name__})"
        application_logging.record_application_failure(RuntimeError(diagnostic))
        logging.error(diagnostic)
        if log_dir:
            with open(os.path.join(log_dir, "error.log"), "a") as f:
                f.write(diagnostic + "\n")
        if isinstance(e, AuditFailureException):
            raise AuditFailureException("GP Connect audit persistence failed") from None
        status_code = e.status_code if isinstance(e, HTTPException) else 500
        msg = f"GP Connect failed at {step}: {e.detail}" if isinstance(e, HTTPException) else diagnostic
        return JSONResponse(status_code=status_code, content={"success": False, "error": msg})
