"""Serializable preparation and conversion jobs. No network I/O in worker jobs.

Only raw dictionaries and small dm+d models cross process boundaries. The FHIR
ownership graph is constructed exactly once, inside the final conversion job.
"""

import json
import logging
from dataclasses import dataclass, field
from time import perf_counter

from fastapi import HTTPException
from fhirclient.models import bundle
from fhirclient.models.coding import Coding
from fhirclient.models.dosage import Dosage

from .convert_mime import base64_xml, document_xml
from .dosage_lookup import mapped_dosages
from .entries.medication import required_dmd_code
from .fhir2ccda import convert_bundle_sync


@dataclass
class ConversionFailure:
    """Transfer error types/steps; retain only deliberately public HTTP details."""

    step: str
    exception_type: str
    validation: bool = False
    status_code: int | None = None
    detail: str | None = None


@dataclass
class PreparedBundle:
    payload: dict
    dmd_codes: list[int]


@dataclass
class ConvertedDocument:
    encoded: str
    debug_xml: str | None = None
    timings: dict[str, float] = field(default_factory=dict)
    resource_count: int = 0


def prepare_bundle(payload: bytes) -> PreparedBundle | ConversionFailure:
    """Decode and find eligible medication concepts without full FHIR models.

    Build only dosage/coding models needed to share the renderer's selection
    rules. Invalid individual medications remain subject to normal validation
    and omission handling in the final job, rather than failing enrichment.
    """
    step = "validate_fhir_bundle.json_decode"
    try:
        decoded = json.loads(payload)
        step = "validate_fhir_bundle.resource_type"
        if not isinstance(decoded, dict) or decoded.get("resourceType") != "Bundle":
            raise ValueError("Expected Bundle")
        step = "validate_fhir_bundle.comment_entries"
        for position, entry in enumerate(decoded.get("entry", [])):
            if "fhir_comments" in entry:
                decoded["entry"].pop(position)
                break
        index = {}
        for entry in decoded.get("entry", []):
            resource = entry.get("resource")
            if not isinstance(resource, dict):
                continue
            index[f"{resource.get('resourceType')}/{resource.get('id')}"] = resource
            if entry.get("fullUrl"):
                index[entry["fullUrl"]] = resource
        codes = []
        for entry in decoded.get("entry", []):
            resource = entry.get("resource", {})
            if resource.get("resourceType") != "List" or resource.get("title") not in (
                "Medications and medical devices",
                "Active Medications",
                "Past Medications",
            ):
                continue
            for item in resource.get("entry", []):
                try:
                    statement = index[item["item"]["reference"]]
                    medication = index[statement["medicationReference"]["reference"]]
                    dosages = mapped_dosages([Dosage(value) for value in statement.get("dosage", [])]).dosages
                    codings = [Coding(value) for value in medication["code"]["coding"]]
                    code = required_dmd_code(dosages, codings)
                    if code is not None:
                        codes.append(code)
                except Exception:
                    continue
        return PreparedBundle(decoded, list(dict.fromkeys(codes)))
    except Exception as exc:
        return ConversionFailure(step, type(exc).__name__, validation=True)


def convert_document(payload, dmd_concepts, options) -> ConvertedDocument | ConversionFailure:
    """Validate, render and serialize one document synchronously in a worker."""
    step = "validate_fhir_bundle.fhir_models"
    timings = {}
    try:
        started = perf_counter()
        fhir_bundle = bundle.Bundle(payload)
        timings["fhir.models"] = (perf_counter() - started) * 1000
        step = "fhir_index.resource_index"
        started = perf_counter()
        index = {}
        for entry in fhir_bundle.entry or []:
            try:
                index[f"{entry.resource.resource_type}/{entry.resource.id}"] = entry.resource
                if entry.fullUrl:
                    index[entry.fullUrl] = entry.resource
            except Exception as exc:
                logging.warning("Skipping FHIR resource (%s)", type(exc).__name__)
        timings["fhir.index"] = (perf_counter() - started) * 1000
        step = "ccda_convert.convert_bundle"
        started = perf_counter()
        document = convert_bundle_sync(
            fhir_bundle,
            index,
            dmd_concepts,
            inclusions=options["inclusions"],
            caching_period=options["caching_period"],
        )
        timings["ccda.convert"] = (perf_counter() - started) * 1000
        step = "ccda_serialize.base64_xml"
        started = perf_counter()
        # Debug XML deliberately retains the old unmodified narrative escaping.
        debug_xml = document_xml(document) if options.get("debug_xml") else None
        encoded = base64_xml(document)
        timings["ccda.serialize"] = (perf_counter() - started) * 1000
        return ConvertedDocument(encoded, debug_xml, timings, len(fhir_bundle.entry or []))
    except Exception as exc:
        return ConversionFailure(
            step,
            type(exc).__name__,
            validation=step.startswith("validate_fhir_bundle"),
            status_code=exc.status_code if isinstance(exc, HTTPException) else None,
            detail=exc.detail if isinstance(exc, HTTPException) else None,
        )
