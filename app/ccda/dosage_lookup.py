"""Read-only lookup generated from the canonical NHS dosage CSV.

Regenerate with scripts/build_dosage_mapping.py; never edit the generated data.
"""

import hashlib
import json
from pathlib import Path
from types import MappingProxyType

from fhirclient.models.dosage import Dosage

MAPPING_SHA256 = "140e2fa36fd0f02497af48b34b7568b38f75748c4021e9c9e9969f0e475275d3"


def normalize_dosage(text: str) -> str:
    """Normalize case/whitespace only; punctuation and word/number variants differ."""
    return " ".join(text.lower().split())


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _copy_template(value):
    if isinstance(value, MappingProxyType):
        return {key: _copy_template(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_copy_template(item) for item in value]
    return value


def _load_mappings() -> MappingProxyType:
    raw = Path(__file__).with_name("data").joinpath("dosage_mapping.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != MAPPING_SHA256:
        raise ValueError("Dosage mapping checksum mismatch; regenerate from the canonical CSV")
    return _freeze({key: value["fhir"] for key, value in json.loads(raw).items()})


# Imported once per application process, with no CSV reads or rebuilding at runtime.
_MAPPINGS = _load_mappings()
_NARRATIVE_AND_METADATA = frozenset({"id", "extension", "sequence", "text", "patientInstruction"})


def lookup_dosage(text: str) -> dict | None:
    """Return an independent source FHIR mapping, preserving the input text."""
    template = _MAPPINGS.get(normalize_dosage(text))
    if template is None:
        return None
    result = _copy_template(template)
    result["text"] = text
    return result


def mapped_dosages(dosages: list[Dosage] | None) -> list[Dosage]:
    """Enrich narrative-only dosages locally without changing source FHIR objects.

    Any existing structured value in the medicine, including an explicit false
    PRN flag, prevents enrichment. Metadata and patient instructions are retained.
    """
    # The CDA converter reads the first dosage even if no dosage was supplied.
    if not dosages:
        return [Dosage()]
    structured = any(
        any(
            getattr(dosage, name) is not None
            for name, json_name, *_ in dosage.elementProperties()
            if json_name not in _NARRATIVE_AND_METADATA
        )
        for dosage in dosages
    )
    if structured:
        return dosages
    result = []
    for dosage in dosages:
        mapping = lookup_dosage(dosage.text) if dosage.text else None
        if mapping is not None:
            result.append(Dosage({**dosage.as_json(), **mapping}))
        else:
            result.append(dosage)
    return result
