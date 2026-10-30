"""Provider Endpoint and qualified Device resolution with separate existing error scopes."""

import logging
import os
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from .. import logging as application_logging
from ..audit import audit
from ..audit.audit import AuditFailureException
from ..audit.models import AuditOutcome, SAMLAttributes
from ..pds.pds import SDSUpstreamError, sds_trace
from ..telemetry import measure
from .types import DiagnosticStep, ProviderRoute


async def resolve_provider(
    gp_ods: Any,
    nhsno: int,
    saml_attrs: SAMLAttributes,
    *,
    request: Request | None,
    log_dir: str | None,
    step: DiagnosticStep,
) -> ProviderRoute | JSONResponse:
    """Resolve the provider without strengthening current SDS cardinality or validation."""
    # 5) Resolve provider Endpoint and MHS Party Key
    fhir_endpoint_url = None
    nhsmhsparty = None
    party_keys = []
    step.value = "sds_endpoint.sds_lookup"
    try:
        with measure("sds.endpoint"):
            endpoint_trace = await sds_trace(gp_ods, endpoint=True)

        if (
            isinstance(endpoint_trace, dict)
            and endpoint_trace.get("resourceType") == "Bundle"
            and endpoint_trace.get("entry") == []
        ):
            step.value = "sds_endpoint.entry_validation"
            msg = f"Unable to resolve GP Connect Structured Record FHIR endpoint in SDS for ODS code {gp_ods}"
            diagnostic = f"{msg} (step: {step.value})"
            logging.info(diagnostic)
            await audit.attempt_audit(
                request=request,
                nhs_number=str(nhsno),
                saml=saml_attrs,
                action="fhir_endpoint_trace",
                outcome=AuditOutcome.fail,
                error_code="502",
                detail={"step": step.value, "reason": "no_gp_connect_structured_record_route"},
            )
            return JSONResponse(status_code=502, content={"success": False, "error": msg})

        step.value = "sds_endpoint.response_validation"
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
            step.value = "sds_endpoint.entry_validation"
            msg = f"Ambiguous SDS Endpoint response for ODS code {gp_ods}"
            diagnostic = f"{msg} (step: {step.value})"
            application_logging.record_application_failure(RuntimeError(diagnostic))
            logging.error(diagnostic)
            if log_dir:
                with open(os.path.join(log_dir, "error.log"), "a") as f:
                    f.write(diagnostic + "\n")
            await audit.attempt_audit(
                request=request,
                nhs_number=str(nhsno),
                saml=saml_attrs,
                action="fhir_endpoint_trace",
                outcome=AuditOutcome.fail,
                error_code="502",
                detail={"step": step.value, "reason": "ambiguous_endpoint_entries"},
            )
            return JSONResponse(status_code=502, content={"success": False, "error": msg})

        step.value = "sds_endpoint.address_extraction"
        entry = entries[0]
        endpoint_resource = entry["resource"]
        if (
            isinstance(endpoint_resource, dict)
            and endpoint_resource.get("resourceType")
            and endpoint_resource.get("resourceType") != "Endpoint"
        ):
            raise ValueError(f"Unable to find FHIR endpoint for ODS code {gp_ods}")
        fhir_endpoint_url = endpoint_resource["address"]
        step.value = "sds_endpoint.address_validation"
        if not isinstance(fhir_endpoint_url, str) or not fhir_endpoint_url.strip():
            raise ValueError(f"Unable to find FHIR endpoint for ODS code {gp_ods}")

        step.value = "sds_endpoint.partykey_extraction"
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
        step.value = "sds_endpoint.partykey_validation"
        if not party_keys:
            raise ValueError(f"Unable to find nhsMhsPartyKey for ODS code {gp_ods}")
        if len(party_keys) > 1:
            raise ValueError(f"Ambiguous nhsMhsPartyKey for ODS code {gp_ods}")
        nhsmhsparty = party_keys[0]

        step.value = "sds_endpoint.success_audit"
        await audit.attempt_audit(
            request=request,
            nhs_number=str(nhsno),
            saml=saml_attrs,
            action="fhir_endpoint_trace",
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
        if step.value == "sds_endpoint.partykey_validation":
            if not party_keys:
                detail["reason"] = "missing_nhs_mhs_party_key"
            elif len(party_keys) > 1:
                detail["reason"] = "ambiguous_nhs_mhs_party_key"
        msg = f"SDS Endpoint resolution failed for ODS code {gp_ods}"
        diagnostic = f"{msg} (step: {step.value}, exception_type: {type(e).__name__})"
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
        step.value = "sds_trace.sds_lookup"
        with measure("sds.trace"):
            asid_trace = await sds_trace(gp_ods, mhsparty=nhsmhsparty)
        step.value = "sds_trace.success_audit"
        await audit.attempt_audit(
            request=request,
            nhs_number=str(nhsno),
            saml=saml_attrs,
            action="sds_trace",
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
        msg = f"SDS Trace failed for ODS code {gp_ods}"
        diagnostic = f"{msg} (step: {step.value}, exception_type: {type(e).__name__})"
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
            action="sds_trace",
            outcome=AuditOutcome.fail,
            error_code=str(status_code),
            detail=detail,
        )
        msg = f"{msg}: {e.detail}" if isinstance(e, (HTTPException, SDSUpstreamError)) else diagnostic
        return JSONResponse(status_code=status_code, content={"success": False, "error": msg})

    step.value = "resolve_asid_partykey.device_extraction"
    try:
        if not isinstance(asid_trace, dict) or asid_trace.get("resourceType") != "Bundle":
            raise ValueError(f"Expected a FHIR Bundle resource from SDS Device lookup for ODS code {gp_ods}")

        raw_entries = asid_trace.get("entry")
        if not isinstance(raw_entries, list):
            raise ValueError(f"Expected entry list in SDS Device lookup for ODS code {gp_ods}")

        device_entries = raw_entries
        if not device_entries:
            step.value = "resolve_asid_partykey.identity_validation"
            msg = f"Unable to find ASID for ODS code {gp_ods}"
            diagnostic = f"{msg} (step: {step.value})"
            application_logging.record_application_failure(RuntimeError(diagnostic))
            logging.error(diagnostic)
            if log_dir:
                with open(os.path.join(log_dir, "error.log"), "a") as f:
                    f.write(diagnostic + "\n")
            await audit.attempt_audit(
                request=request,
                nhs_number=str(nhsno),
                saml=saml_attrs,
                action="resolve_asid_partykey",
                outcome=AuditOutcome.fail,
                error_code="500",
                detail={"step": step.value, "message": msg},
            )
            return JSONResponse(status_code=500, content={"success": False, "error": msg})

        if len(device_entries) > 1:
            step.value = "resolve_asid_partykey.device_validation"
            msg = f"Ambiguous SDS Device response for ODS code {gp_ods}"
            diagnostic = f"{msg} (step: {step.value})"
            application_logging.record_application_failure(RuntimeError(diagnostic))
            logging.error(diagnostic)
            if log_dir:
                with open(os.path.join(log_dir, "error.log"), "a") as f:
                    f.write(diagnostic + "\n")
            await audit.attempt_audit(
                request=request,
                nhs_number=str(nhsno),
                saml=saml_attrs,
                action="resolve_asid_partykey",
                outcome=AuditOutcome.fail,
                error_code="500",
                detail={"step": step.value, "message": msg, "reason": "ambiguous_device_entries"},
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

        step.value = "resolve_asid_partykey.identifier_extraction"
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
        step.value = "resolve_asid_partykey.identity_validation"
        if not asids:
            msg = f"Unable to find ASID for ODS code {gp_ods}"
            diagnostic = f"{msg} (step: {step.value})"
            application_logging.record_application_failure(RuntimeError(diagnostic))
            logging.error(diagnostic)
            if log_dir:
                with open(os.path.join(log_dir, "error.log"), "a") as f:
                    f.write(diagnostic + "\n")
            await audit.attempt_audit(
                request=request,
                nhs_number=str(nhsno),
                saml=saml_attrs,
                action="resolve_asid_partykey",
                outcome=AuditOutcome.fail,
                error_code="500",
                detail={"step": step.value, "message": msg},
            )
            return JSONResponse(status_code=500, content={"success": False, "error": msg})
        if len(asids) > 1:
            msg = f"Ambiguous ASID for ODS code {gp_ods}"
            diagnostic = f"{msg} (step: {step.value})"
            application_logging.record_application_failure(RuntimeError(diagnostic))
            logging.error(diagnostic)
            if log_dir:
                with open(os.path.join(log_dir, "error.log"), "a") as f:
                    f.write(diagnostic + "\n")
            await audit.attempt_audit(
                request=request,
                nhs_number=str(nhsno),
                saml=saml_attrs,
                action="resolve_asid_partykey",
                outcome=AuditOutcome.fail,
                error_code="500",
                detail={"step": step.value, "message": msg},
            )
            return JSONResponse(status_code=500, content={"success": False, "error": msg})
        asid = asids[0]
    except AuditFailureException:
        raise
    except Exception as e:
        status_code = e.status_code if isinstance(e, HTTPException) else 500
        msg = "Unable to parse SDS trace response"
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
            action="resolve_asid_partykey",
            outcome=AuditOutcome.fail,
            error_code=str(status_code),
            detail={"step": step.value, "exception_type": type(e).__name__},
        )
        msg = f"{msg}: {e.detail}" if isinstance(e, HTTPException) else diagnostic
        return JSONResponse(status_code=status_code, content={"success": False, "error": msg})

    return ProviderRoute(endpoint_url=fhir_endpoint_url, asid=asid)
