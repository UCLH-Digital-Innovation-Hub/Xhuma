"""FHIR validation, C-CDA generation and publication with existing failure boundaries."""

import json
import logging
import os
import time
from datetime import timedelta
from uuid import uuid4

import httpx
import xmltodict
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from fhirclient.models import bundle

from .. import logging as application_logging
from ..audit import audit
from ..audit.audit import AuditFailureException
from ..audit.models import AuditOutcome, SAMLAttributes
from ..ccda.convert_mime import base64_xml
from ..ccda.fhir2ccda import convert_bundle
from ..redis_connect import redis_client
from ..settings import diag_compare_enabled, diag_ttl_minutes
from ..telemetry import measure
from .types import DiagnosticStep


async def process_document(
    resp: httpx.Response,
    nhsno: int,
    saml_attrs: SAMLAttributes,
    *,
    ssp_trace_id: str | None,
    request: Request | None,
    log_dir: str | None,
    step: DiagnosticStep,
) -> str | JSONResponse:
    """Return the published document ID or the existing early failure response."""
    # 10) Validate the entire upstream payload before conversion or caching.
    step.value = "validate_fhir_bundle.response_content"
    try:
        step.value = "validate_fhir_bundle.json_decode"
        with measure("fhir.decode", input_bytes=len(resp.content)):
            scr_bundle = json.loads(resp.text)
        step.value = "validate_fhir_bundle.resource_type"
        if not isinstance(scr_bundle, dict) or scr_bundle.get("resourceType") != "Bundle":
            raise ValueError("Expected a FHIR Bundle resource")
        # Retain the existing compatibility handling for standalone comment entries.
        step.value = "validate_fhir_bundle.comment_entries"
        for j, entry in enumerate(scr_bundle.get("entry", [])):
            if "fhir_comments" in entry:
                scr_bundle["entry"].pop(j)
                break
        step.value = "validate_fhir_bundle.fhir_models"
        with measure("fhir.models", resource_count=len(scr_bundle.get("entry", []))):
            fhir_bundle = bundle.Bundle(scr_bundle)
    except AuditFailureException:
        raise
    except Exception as e:
        status_code = e.status_code if isinstance(e, HTTPException) else 502
        msg = "FHIR bundle malformed"
        diagnostic = f"{msg} (step: {step.value}, exception_type: {type(e).__name__})"
        application_logging.record_application_failure(RuntimeError(diagnostic))
        logging.error(diagnostic)
        if log_dir:
            with open(os.path.join(log_dir, "error.log"), "a") as f:
                f.write(diagnostic + "\n")
        await audit.attempt_audit(
            request=request,
            request_id=ssp_trace_id,
            nhs_number=str(nhsno),
            saml=saml_attrs,
            action="validate_fhir_bundle",
            outcome=AuditOutcome.fail,
            error_code=str(status_code),
            detail={"step": step.value, "exception_type": type(e).__name__},
        )
        msg = "FHIR bundle malformed" + f": {e.detail}" if isinstance(e, HTTPException) else diagnostic
        return JSONResponse(status_code=status_code, content={"success": False, "error": msg})

    # index resources for resolution
    step.value = "fhir_index.resource_index"
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

    step.value = "ccda_convert.start_timer"
    start_time = time.perf_counter()
    try:
        step.value = "ccda_convert.convert_bundle"
        with measure("ccda.convert", span_name="FHIR2CCDA.convert_bundle", resource_count=len(fhir_bundle.entry or [])):
            xml_ccda = await convert_bundle(fhir_bundle, bundle_index)

        step.value = "ccda_convert.duration"
        duration_ms = (time.perf_counter() - start_time) * 1000
        logging.info(f"FHIR2CCDA conversion completed in {duration_ms:.2f}ms")
    except AuditFailureException:
        raise
    except Exception as e:
        status_code = e.status_code if isinstance(e, HTTPException) else 500
        msg = "Failed to convert FHIR Bundle to CCDA"
        diagnostic = f"{msg} (step: {step.value}, exception_type: {type(e).__name__})"
        application_logging.record_application_failure(RuntimeError(diagnostic))
        logging.error(diagnostic)
        if log_dir:
            with open(os.path.join(log_dir, "error.log"), "a") as f:
                f.write(diagnostic + "\n")
        msg = f"{msg}: {e.detail}" if isinstance(e, HTTPException) else diagnostic
        return JSONResponse(status_code=status_code, content={"success": False, "error": msg})

    if log_dir:
        step.value = "ccda_serialize.xml_debug_file"
        # CodeQL static analysis requires explicit integer casting to drop path traversal taint
        safe_nhsno = str(int(nhsno))
        with open(os.path.join(log_dir, f"{safe_nhsno}.xml"), "w") as output:
            output.write(xmltodict.unparse(xml_ccda, pretty=True))

    step.value = "ccda_serialize.base64_xml"
    with measure("ccda.serialize") as span:
        xop = base64_xml(xml_ccda)
        step.value = "ccda_serialize.document_size"
        span.set_attribute("document.base64_bytes", len(xop))
    step.value = "store_ccda.document_id"
    doc_uuid = str(uuid4())
    step.value = "store_ccda.success_audit"
    await audit.attempt_audit(
        request=request,
        nhs_number=str(nhsno),
        saml=saml_attrs,
        action="store_ccda",
        outcome=AuditOutcome.ok,
        document_id=doc_uuid,
        detail={"step": step.value},
    )

    step.value = "ccda_cache.expiry_configuration"
    if request and hasattr(request, "app") and hasattr(request.app.state, "ccda_expiry_hours"):
        expiry_hours = request.app.state.ccda_expiry_hours
    else:
        expiry_hours = float(os.getenv("CCDA_EXPIRY_HOURS", "4"))

    step.value = "ccda_cache.cache_ttl"
    cache_ttl = timedelta(hours=expiry_hours)

    # Use a pipeline to write all keys atomically with a consistent TTL
    step.value = "ccda_cache.pipeline_open"
    async with redis_client.pipeline() as pipe:
        step.value = "ccda_cache.queue_patient_document"
        pipe.setex(str(nhsno), cache_ttl, doc_uuid)
        document_key = f"document:{doc_uuid}"
        step.value = "ccda_cache.queue_document_content"
        pipe.hset(document_key, mapping={"nhs_number": str(nhsno), "content": xop})
        step.value = "ccda_cache.queue_document_expiry"
        pipe.expire(document_key, cache_ttl)
        step.value = "ccda_cache.pipeline_execute"
        with measure("ccda.cache.write", document_bytes=len(xop)):
            await pipe.execute()
        step.value = "ccda_cache.pipeline_close"

    # Diagnostics: when enabled (always in dev/local/int, opt-in via DIAG_COMPARE_ENABLED in prd),
    # capture the received FHIR bundle and the serialised C-CDA, keyed off the same doc_uuid, so
    # the gated /_diag/compare viewer can show "as received" next to "as sent". Both payloads are
    # PHI, so they use a short, independent TTL (DIAG_TTL_MIN) and never run unless the gate is on.
    if diag_compare_enabled():
        step.value = "diag_capture"
        try:
            diag_ttl = timedelta(minutes=diag_ttl_minutes())
            bundle_json = json.dumps(scr_bundle, indent=2, ensure_ascii=False)
            ccda_xml = xmltodict.unparse(xml_ccda, pretty=True)
            async with redis_client.pipeline() as diag_pipe:
                diag_pipe.setex(f"diag:bundle:{doc_uuid}", diag_ttl, bundle_json)
                diag_pipe.setex(f"diag:xml:{doc_uuid}", diag_ttl, ccda_xml)
                await diag_pipe.execute()
        except Exception as diag_err:  # diagnostics must never break the main response
            logging.warning("diag capture skipped for doc %s: %s", doc_uuid, diag_err)

    # only write the xml if dev
    if os.getenv("ENV", "prod").lower() in ("dev", "local"):
        step.value = "ccda_serialize.local_xml_file"
        with open(f"{int(nhsno)!s}.xml", "w") as output:
            output.write(xmltodict.unparse(xml_ccda, pretty=True))

    return doc_uuid
