"""FHIR validation, C-CDA generation and publication with existing failure boundaries."""

import logging
import os
from datetime import timedelta
from uuid import uuid4

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse

from .. import logging as application_logging
from ..audit import audit
from ..audit.models import AuditOutcome, SAMLAttributes
from ..ccda.conversion import ConversionFailure, convert_document, prepare_bundle
from ..ccda.dmd import load_medication_dmd
from ..ccda.workers import ConversionWorkers
from ..gp_connect_config import get_gp_connect_inclusions
from ..redis_connect import redis_client
from ..telemetry import measure, stage_duration
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
    # Jobs contain CPU work only. dm+d and publication stay on this event loop.
    # HTTP requests reuse the lifespan pool; standalone scripts own a short-lived
    # pool so they do not leak processes or require a FastAPI request object.
    workers = (
        getattr(request.app.state, "conversion_workers", None)
        if request is not None and hasattr(request, "app")
        else None
    )
    owned_workers = workers is None
    if owned_workers:
        workers = ConversionWorkers.from_environment()
    try:
        step.value = "validate_fhir_bundle.prepare"
        with measure("fhir.prepare", input_bytes=len(resp.content)):
            prepared = await run_job(workers, prepare_bundle, resp.content)

        step.value = "ccda_convert.medication_dmd"
        with measure("terminology.prepare", concept_count=len(prepared.dmd_codes)):
            dmd_concepts = await load_medication_dmd(prepared.dmd_codes)

        options = {
            "inclusions": get_gp_connect_inclusions(),
            "caching_period": os.getenv("CCDA_CACHING_PERIOD", "24 hours"),
            "debug_xml": bool(log_dir) or os.getenv("ENV", "prod").lower() in ("dev", "local"),
        }
        step.value = "ccda_convert.worker"
        with measure("ccda.document") as span:
            result = await run_job(workers, convert_document, prepared.payload, dmd_concepts, options)
            span.set_attribute("resource_count", result.resource_count)
            span.set_attribute("document.base64_bytes", len(result.encoded))
        # Worker telemetry providers are intentionally not configured. Publish
        # measured CPU-stage durations from the API process, without PHI labels.
        for stage, elapsed in result.timings.items():
            stage_duration.record(elapsed, {"stage": stage, "outcome": "ok"})
        logging.info("FHIR2CCDA conversion completed in %.2fms", result.timings.get("ccda.convert", 0))
    except ConversionJobError as exc:
        return await conversion_failure(exc.failure, nhsno, saml_attrs, request, ssp_trace_id, log_dir, step)
    finally:
        if owned_workers:
            await workers.close()

    if log_dir:
        step.value = "ccda_serialize.xml_debug_file"
        with open(os.path.join(log_dir, f"{int(nhsno)!s}.xml"), "w") as output:
            output.write(result.debug_xml)

    xop = result.encoded
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

    # only write the xml if dev
    if os.getenv("ENV", "prod").lower() in ("dev", "local"):
        step.value = "ccda_serialize.local_xml_file"
        with open(f"{int(nhsno)!s}.xml", "w") as output:
            output.write(result.debug_xml)

    return doc_uuid


async def conversion_failure(failure, nhsno, saml_attrs, request, trace_id, log_dir, step):
    """Restore the established validation audit and sanitized error boundary."""
    step.value = failure.step
    status_code = failure.status_code or (502 if failure.validation else 500)
    message = "FHIR bundle malformed" if failure.validation else "Failed to convert FHIR Bundle to CCDA"
    diagnostic = f"{message} (step: {step.value}, exception_type: {failure.exception_type})"
    application_logging.record_application_failure(RuntimeError(diagnostic))
    logging.error(diagnostic)
    if log_dir:
        with open(os.path.join(log_dir, "error.log"), "a") as output:
            output.write(diagnostic + "\n")
    if failure.validation:
        await audit.attempt_audit(
            request=request,
            request_id=trace_id,
            nhs_number=str(nhsno),
            saml=saml_attrs,
            action="validate_fhir_bundle",
            outcome=AuditOutcome.fail,
            error_code=str(status_code),
            detail={"step": step.value, "exception_type": failure.exception_type},
        )
    error = f"{message}: {failure.detail}" if failure.detail is not None else diagnostic
    return JSONResponse(status_code=status_code, content={"success": False, "error": error})


class ConversionJobError(Exception):
    """Local exception marks the surrounding API span as failed."""

    def __init__(self, failure: ConversionFailure):
        self.failure = failure
        super().__init__(failure.exception_type)


async def run_job(workers, function, *args):
    result = await workers.run(function, *args)
    if isinstance(result, ConversionFailure):
        raise ConversionJobError(result)
    return result
