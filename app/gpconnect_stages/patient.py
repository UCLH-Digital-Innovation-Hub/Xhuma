"""Patient validation, PDS lookup, restriction check and raw GP ODS extraction."""

import logging
import os
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from .. import logging as application_logging
from ..audit import audit
from ..audit.audit import AuditFailureException
from ..audit.models import AuditOutcome, SAMLAttributes
from ..ccda.helpers import validateNHSnumber
from ..pds.pds import lookup_patient
from ..telemetry import measure
from .types import DiagnosticStep


async def resolve_patient(
    nhsno: int,
    saml_attrs: SAMLAttributes,
    *,
    request: Request | None,
    log_dir: str | None,
    step: DiagnosticStep,
) -> Any:
    """Return the unvalidated JSON ODS value, or the existing early JSONResponse.

    PDS owns patient caching. Only the first security/GP entries are read here.
    """
    # 1) Validate NHS number
    if validateNHSnumber(nhsno) is False:
        msg = f"{nhsno} is not a valid NHS number"
        await audit.attempt_audit(
            request=request,
            nhs_number=str(nhsno),
            saml=saml_attrs,
            action="validate_nhs_number",
            outcome=AuditOutcome.fail,
            error_code="400",
            detail={"step": step.value},
        )
        return JSONResponse(status_code=400, content={"success": False, "error": msg})

    # 2) PDS lookup
    try:
        step.value = "pds_lookup.lookup_patient"
        with measure("pds.lookup"):
            pds_search = await lookup_patient(nhsno, request=request, saml=saml_attrs)
    except AuditFailureException:
        raise
    except HTTPException as e:
        diagnostic = (
            f"PDS lookup failed (HTTP {e.status_code}) (step: {step.value}, exception_type: {type(e).__name__})"
        )
        if e.status_code != 404:
            application_logging.record_application_failure(RuntimeError(diagnostic))
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
        diagnostic = f"{msg} (step: {step.value}, exception_type: {type(e).__name__})"
        application_logging.record_application_failure(RuntimeError(diagnostic))
        logging.error(diagnostic)
        if log_dir:
            with open(os.path.join(log_dir, "error.log"), "a") as f:
                f.write(diagnostic + "\n")
        msg = f"{msg}: {e.detail}" if isinstance(e, HTTPException) else diagnostic
        return JSONResponse(status_code=status_code, content={"success": False, "error": msg})

    # 3) Security/U flag
    step.value = "check_restriction.security_extraction"
    try:
        security_code = pds_search["meta"]["security"][0]["code"]
    except Exception as e:
        diagnostic = f"Unable to read PDS security metadata (step: {step.value}, exception_type: {type(e).__name__})"
        application_logging.record_application_failure(RuntimeError(diagnostic))
        logging.error(diagnostic)
        if log_dir:
            with open(os.path.join(log_dir, "error.log"), "a") as f:
                f.write(diagnostic + "\n")
        security_code = None

    if security_code != "U":
        step.value = "check_restriction.restriction_check"
        msg = "Patient is not unrestricted, access to GP Connect is not permitted"
        await audit.attempt_audit(
            request=request,
            nhs_number=str(nhsno),
            saml=saml_attrs,
            action="check_restriction",
            outcome=AuditOutcome.deny,
            error_code="403",
            detail={"step": step.value},
        )
        diagnostic = f"Patient is restricted (step: {step.value})"
        logging.error(diagnostic)
        if log_dir:
            with open(os.path.join(log_dir, "error.log"), "a") as f:
                f.write(diagnostic + "\n")
        return JSONResponse(status_code=403, content={"success": False, "error": msg})

    # 4) Resolve GP ODS
    step.value = "resolve_gp_ods.ods_extraction"
    try:
        gp_ods = pds_search["generalPractitioner"][0]["identifier"]["value"]
    except AuditFailureException:
        raise
    except Exception as e:
        status_code = e.status_code if isinstance(e, HTTPException) else 502
        msg = "Unable to read GP ODS from PDS response"
        diagnostic = f"{msg} (step: {step.value}, exception_type: {type(e).__name__})"
        application_logging.record_application_failure(RuntimeError(diagnostic))
        logging.error(diagnostic)
        if log_dir:
            with open(os.path.join(log_dir, "error.log"), "a") as f:
                f.write(diagnostic + "\n")
        await audit.attempt_audit(
            request=request,
            nhs_number=str(nhsno),
            saml=saml_attrs,
            action="resolve_gp_ods",
            outcome=AuditOutcome.fail,
            error_code=str(status_code),
            detail={"step": step.value, "exception_type": type(e).__name__},
        )
        msg = f"{msg}: {e.detail}" if isinstance(e, HTTPException) else diagnostic
        return JSONResponse(status_code=status_code, content={"success": False, "error": msg})

    return gp_ods
