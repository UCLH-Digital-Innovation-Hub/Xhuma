#!/usr/bin/env python3
"""Offline strict FHIR diagnostic. Exit codes: 0 valid, 1 invalid, 2 incomplete.

Usage: uv run python scripts/diagnose_fhir_bundle.py 200_response.json
Only structural metadata is emitted; input files are never written.
"""

import json
import logging
import re
import sys
from collections import Counter
from copy import deepcopy
from pathlib import Path

from fhirclient.models.bundle import Bundle
from fhirclient.models.fhirabstractbase import FHIRValidationError
from fhirclient.models.fhirabstractresource import FHIRAbstractResource
from fhirclient.models.fhirelementfactory import FHIRElementFactory

UNKNOWN = ("<unknown>", "unknown_validation_error")
INCOMPLETE = "Diagnostic incomplete: validation failure could not be safely classified."


def failures(error, model, path=()):
    """Read structured paths; emit only properties verified against model metadata."""
    if isinstance(error, FHIRValidationError):
        for part in (error.path or "").split("."):
            if not part or re.fullmatch(r"[0-9]+", part):
                continue
            properties = {p[0]: p for p in model.elementProperties()} if hasattr(model, "elementProperties") else {}
            if part not in properties:
                yield UNKNOWN
                return
            prop = properties[part]
            path += (prop[1],)
            model = prop[2]() if hasattr(prop[2], "elementProperties") else None
        for child in error.errors:
            yield from failures(child, model, path)
    elif type(error) is KeyError and len(error.args) == 1 and isinstance(error.args[0], str):
        # fhirclient 3.0.x has no structured missing-property attribute. Match
        # only its exact template, then check the field against this model.
        match = re.fullmatch(
            r'Non-optional property "([A-Za-z][A-Za-z0-9]*)" on '
            r"<fhirclient\.models\.[a-z]+\.[A-Za-z]+ object at 0x[0-9a-f]+> is missing",
            error.args[0],
        )
        required = {p[4] or p[1] for p in model.elementProperties() if p[5]} if model else set()
        if match and match[1] in required:
            yield (".".join((*path, match[1])), "missing_required")
        else:
            yield UNKNOWN
    elif type(error) is TypeError and path:
        yield (".".join(path), "invalid_type")
    else:
        yield UNKNOWN


def resource_model(entry):
    resource = entry.get("resource") if isinstance(entry, dict) else None
    kind = resource.get("resourceType") if isinstance(resource, dict) else None
    if isinstance(kind, str):
        model = FHIRElementFactory.instantiate(kind, None)
        if isinstance(model, FHIRAbstractResource) and model.resource_type == kind:
            return model
    return None


def entry_failures(error, model):
    # One-entry Bundle construction yields entry.0 -> resource -> model errors.
    if isinstance(error, FHIRValidationError):
        for entry_error in error.errors:
            if isinstance(entry_error, FHIRValidationError) and entry_error.path == "entry.0":
                for resource_error in entry_error.errors:
                    if isinstance(resource_error, FHIRValidationError) and resource_error.path == "resource" and model:
                        for child in resource_error.errors:
                            yield from failures(child, model)
                    else:
                        yield UNKNOWN
            else:
                yield UNKNOWN
    else:
        yield UNKNOWN


class ValidationWarnings(logging.Handler):
    """Discard library log content without formatting or retaining it."""

    seen = False

    def emit(self, record):
        self.seen = True


def diagnose(data):
    # fhirclient's date parser can log raw values even in strict mode. Keep
    # library logs out of the operator output and flag any such warning.
    logger = logging.getLogger("fhirclient")
    previous = logger.handlers, logger.propagate, logger.level
    warnings = ValidationWarnings()
    logger.handlers, logger.propagate = [warnings], False
    logger.setLevel(logging.WARNING)
    try:
        report, status = _diagnose(data)
    finally:
        logger.handlers, logger.propagate, logger.level = previous
    if warnings.seen:
        report += "\nDiagnostic incomplete: validator emitted suppressed warnings."
        status = 2
    return report, status


def _diagnose(data):
    """Return a safe report and exit status, without modifying the decoded input."""
    if not isinstance(data, dict) or data.get("resourceType") != "Bundle":
        return "Diagnostic incomplete: expected a FHIR Bundle.", 2
    data = deepcopy(data)
    entries = data.get("entry", [])
    if not isinstance(entries, list):
        return "Diagnostic incomplete: Bundle entry must be a list.", 2
    original_count = len(entries)
    removed = 0
    # Exactly mirror document.py: remove only the first matching entry.
    for index, entry in enumerate(entries):
        if isinstance(entry, dict) and "fhir_comments" in entry:
            entries.pop(index)
            removed = 1
            break
    whole = "PASS"
    envelope_failure = False
    try:
        Bundle(data)
    except FHIRValidationError as error:
        whole = "FAIL (FHIRValidationError)"
        envelope_failure = any(
            not isinstance(child, FHIRValidationError) or not re.fullmatch(r"entry\.[0-9]+", child.path or "")
            for child in error.errors
        )
    except Exception:
        whole = "FAIL (unknown_validation_error)"
    counts, reasons = Counter(), Counter()
    if whole != "PASS":
        for entry in entries:
            try:
                Bundle({"resourceType": "Bundle", "type": "collection", "entry": [entry]})
            except Exception as error:
                model = resource_model(entry)
                kind = model.resource_type if model else "<unknown>"
                counts[kind] += 1
                reasons.update((kind, *reason) for reason in set(entry_failures(error, model)) or {UNKNOWN})
    incomplete = whole != "PASS" and (
        envelope_failure
        or not counts
        or whole == "FAIL (unknown_validation_error)"
        or any(r[2] == UNKNOWN[1] for r in reasons)
    )
    lines = [
        "FHIR BUNDLE STRUCTURAL DIAGNOSTIC",
        f"Bundle entries : {original_count}",
        f"Comment entries excluded (compatibility): {removed}",
        f"Whole bundle   : {whole}",
        f"Invalid entries: {sum(counts.values())}",
        "",
        "Resource type       Count",
        *(f"{kind:<20} {count}" for kind, count in sorted(counts.items())),
        "",
        "Structural failures (counts are entries, not occurrences)",
        *(f"{kind:<20} {path:<32} {failure:<26} {count}" for (kind, path, failure), count in sorted(reasons.items())),
    ]
    if incomplete:
        lines.append(INCOMPLETE)
    return "\n".join(lines), 2 if incomplete else int(whole != "PASS")


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or args[0] in ("-h", "--help"):
        print("Usage: uv run python scripts/diagnose_fhir_bundle.py 200_response.json")
        return 0 if args in (["-h"], ["--help"]) else 2
    try:
        report, status = diagnose(json.loads(Path(args[0]).read_bytes()))
    except Exception:
        # Paths, JSON errors and unexpected validator exceptions can contain PHI.
        print("Diagnostic incomplete: input could not be safely analysed.")
        return 2
    print(report)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
