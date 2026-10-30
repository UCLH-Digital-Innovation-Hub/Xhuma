"""Canonical-source fidelity, immutable lookup, and medication fallback behaviour."""

import csv
import hashlib
import importlib
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fhirclient.models.dosage import Dosage
from fhirclient.models.medication import Medication
from fhirclient.models.medicationrequest import MedicationRequest
from fhirclient.models.medicationstatement import MedicationStatement

from app.ccda import dosage_lookup
from app.ccda.entries import medication
from app.ccda.models.dmd import DMDConcept, VPIProperty
from scripts.build_dosage_mapping import DEFAULT_OUTPUT, DEFAULT_SOURCE, SOURCE_SHA256, consolidate


def test_rebuild_is_reproducible_from_canonical_source(tmp_path):
    assert hashlib.sha256(DEFAULT_SOURCE.read_bytes()).hexdigest() == SOURCE_SHA256
    provenance = consolidate(DEFAULT_SOURCE, tmp_path)
    assert provenance["source_rows"] == 45902
    assert provenance["consolidated_rows"] == 1693
    for name in ("dosage_mapping.json", "dosage_provenance.json"):
        assert (tmp_path / name).read_bytes() == (DEFAULT_OUTPUT / name).read_bytes()


def test_modified_source_is_rejected_without_touching_outputs(tmp_path):
    source = tmp_path / "modified.csv"
    source.write_bytes(DEFAULT_SOURCE.read_bytes() + b"\n")
    output = tmp_path / "generated"
    output.mkdir()
    sentinel = output / "dosage_mapping.json"
    sentinel.write_text("unchanged")
    with pytest.raises(ValueError, match="Source SHA-256 mismatch"):
        consolidate(source, output)
    assert sentinel.read_text() == "unchanged"
    assert not (output / "dosage_provenance.json").exists()


def test_every_source_row_retains_its_complete_fhir_and_bucket():
    generated = json.loads((DEFAULT_OUTPUT / "dosage_mapping.json").read_bytes())
    validated = set()
    with DEFAULT_SOURCE.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            expected = json.loads(row["fhir_json"])
            assert dosage_lookup.lookup_dosage(row["dosage"]) == expected
            key = dosage_lookup.normalize_dosage(row["dosage"])
            assert generated[key]["buckets"] == row["buckets"]
            if key not in validated:
                # All templates must be accepted by the application's STU3 client.
                Dosage(expected)
                validated.add(key)
    assert len(validated) == len(generated) == 1693


def test_lookup_preserves_text_is_isolated_and_does_not_reload(monkeypatch):
    def unexpected_read(*args, **kwargs):
        raise AssertionError("Mapping must be loaded once, not during lookup")

    monkeypatch.setattr(Path, "read_bytes", unexpected_read)
    text = "  ONE   twice A day \t"
    first = dosage_lookup.lookup_dosage(text)
    assert first["text"] == text
    assert first["timing"]["repeat"]["frequency"] == 2
    first["timing"]["repeat"]["frequency"] = 999
    first["doseQuantity"]["value"] = 999
    second = dosage_lookup.lookup_dosage(text)
    assert second["timing"]["repeat"]["frequency"] == 2
    assert second["doseQuantity"]["value"] == 1
    with pytest.raises(TypeError):
        dosage_lookup._MAPPINGS["one twice a day"] = "{}"
    with pytest.raises(TypeError):
        dosage_lookup._MAPPINGS["one twice a day"]["timing"]["repeat"]["frequency"] = 999


def test_modified_generated_mapping_is_rejected(monkeypatch):
    monkeypatch.setattr(Path, "read_bytes", lambda _: b"{}")
    with pytest.raises(ValueError, match="Dosage mapping checksum mismatch"):
        dosage_lookup._load_mappings()


@pytest.mark.parametrize("text", ["", " \t", "unmapped dosage xyz", "one twice a day!"])
def test_unknown_or_differently_punctuated_text_is_not_inferred(text):
    assert dosage_lookup.lookup_dosage(text) is None


@pytest.mark.parametrize(
    "structured",
    [
        {"doseQuantity": {"value": 7}},
        {"doseRange": {"low": {"value": 1}, "high": {"value": 2}}},
        {"timing": {"repeat": {"frequency": 3, "period": 1, "periodUnit": "d"}}},
        {"timing": {}},
        {"asNeededBoolean": False},
        {"asNeededBoolean": True},
        {"route": {"text": "source route"}},
        {"method": {"text": "source method"}},
        {"site": {"text": "source site"}},
        {"additionalInstruction": [{"text": "source instruction"}]},
        {"maxDosePerAdministration": {"value": 4}},
        {"rateQuantity": {"value": 2}},
    ],
)
def test_existing_structured_dosage_is_never_enriched_or_overwritten(structured):
    original = Dosage({"text": "one twice a day", **structured})
    before = original.as_json()
    assert dosage_lookup.mapped_dosages([original]) == [original]
    assert original.as_json() == before


def test_multiple_dosages_preserve_metadata_and_source_objects():
    originals = [
        Dosage(
            {
                "id": "dose-1",
                "sequence": 2,
                "text": "ONE twice a day",
                "patientInstruction": "Patient-specific instruction",
                "extension": [{"url": "https://example.test/source", "valueString": "retained"}],
            }
        ),
        Dosage({"text": "one daily"}),
        Dosage({"text": "unmapped dosage xyz"}),
    ]
    before = [dosage.as_json() for dosage in originals]
    result = dosage_lookup.mapped_dosages(originals)
    assert len(result) == 3
    assert result[0] is not originals[0]
    enriched = result[0].as_json()
    assert enriched == {**before[0], **dosage_lookup.lookup_dosage(before[0]["text"])}
    assert result[1].doseQuantity.value == 1
    assert result[1].timing.repeat.frequency == 1
    assert result[2] is originals[2]
    result[0].extension[0].valueString = "modified copy"
    assert [dosage.as_json() for dosage in originals] == before
    assert result.mapped_indices == frozenset({0, 1})
    assert result.freetext_mapped is True
    assert not hasattr(result[0], "_freetext_mapped")
    assert not hasattr(result[1], "_freetext_mapped")
    assert not hasattr(result[2], "_freetext_mapped")
    assert dosage_lookup.is_freetext_mapped(result) is True


def test_a_medicine_with_any_structured_dosage_is_not_enriched():
    originals = [
        Dosage({"text": "one twice a day"}),
        Dosage({"text": "one daily", "doseQuantity": {"value": 5}}),
    ]
    before = [dosage.as_json() for dosage in originals]
    result = dosage_lookup.mapped_dosages(originals)
    assert result.dosages is originals
    assert not result.freetext_mapped
    assert result.mapped_indices == frozenset()
    assert [dosage.as_json() for dosage in originals] == before


@pytest.fixture
def medicine_source(monkeypatch):
    statement = MedicationStatement(
        {
            "id": "statement",
            "identifier": [{"system": "https://example.test/prescriptions", "value": "statement"}],
            "status": "active",
            "taken": "unk",
            "subject": {"reference": "Patient/1"},
            "medicationReference": {"reference": "Medication/1"},
            "basedOn": [{"reference": "MedicationRequest/1"}],
            "effectivePeriod": {"start": "2026-10-06"},
            "dosage": [{"text": "  ONE   twice A day ", "patientInstruction": "Retain this instruction"}],
        }
    )
    index = {
        "Medication/1": Medication({"id": "1", "code": {"coding": [{"code": "123", "display": "Test medicine"}]}}),
        "MedicationRequest/1": MedicationRequest(
            {
                "id": "1",
                "status": "active",
                "intent": "order",
                "subject": {"reference": "Patient/1"},
                "medicationReference": {"reference": "Medication/1"},
            }
        ),
    }
    dmd = AsyncMock(return_value=DMDConcept(concept_id=123, valueString="Test medicine"))
    monkeypatch.setattr(importlib.import_module("app.ccda.entries.medication"), "dmd_lookup", dmd)
    return statement, index, dmd


@pytest.mark.asyncio
async def test_fallback_reaches_cda_without_mutating_fhir_or_cached_mapping(medicine_source):
    statement, index, dmd = medicine_source
    # Verify mapped unitless doses still reach the existing dm+d conversion path.
    dmd.return_value.vpi = VPIProperty(value=10, unit="mg")
    before = statement.as_json()
    request_before = index["MedicationRequest/1"].as_json()
    for _ in range(2):
        result = await medication(statement, index)
        cda = result.entry["substanceAdministration"]
        assert cda["doseQuantity"]["@value"] == 10
        assert cda["doseQuantity"]["@unit"] == "mg"
        assert cda["effectiveTime"][1]["period"]["@value"] == 0.5
        assert cda["effectiveTime"][1]["period"]["@unit"] == "d"
        assert "ONE   twice A day" in result.row[5]
        assert "Retain this instruction" in result.row[5]
    assert statement.as_json() == before
    assert index["MedicationRequest/1"].as_json() == request_before
    assert dosage_lookup.lookup_dosage("one twice a day")["doseQuantity"]["value"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("dosages", [None, [], [Dosage({"text": "unmapped dosage xyz"})]])
async def test_missing_and_unmapped_dosage_convert_without_inferred_structure(medicine_source, dosages):
    statement, index, dmd = medicine_source
    statement.dosage = dosages
    before = statement.as_json()
    result = await medication(statement, index)
    cda = result.entry["substanceAdministration"]
    assert "doseQuantity" not in cda
    assert len(cda["effectiveTime"]) == 1
    dmd.assert_not_awaited()
    assert statement.as_json() == before


def test_dosage_mapping_result_metadata_and_provenance():
    # Verify explicit Xhuma-owned provenance and absence of ad-hoc attributes on Dosage
    originals = [
        Dosage({"text": "unmapped first"}),
        Dosage({"text": "one twice a day"}),
        Dosage({"text": "unmapped third"}),
    ]
    result = dosage_lookup.mapped_dosages(originals)

    assert isinstance(result, dosage_lookup.DosageMappingResult)
    assert result.freetext_mapped is True
    assert result.mapped_indices == frozenset({1})
    assert len(result) == 3
    assert [d for d in result] == result.dosages
    assert result[1].doseQuantity.value == 1

    # Original FHIR objects are untouched
    assert result[0] is originals[0]
    assert result[1] is not originals[1]
    assert result[2] is originals[2]

    # No arbitrary/private attributes added to Dosage instances
    for d in result:
        assert not hasattr(d, "_freetext_mapped")

    # Sequence equality check
    assert result == result.dosages

    # Empty and None input handling
    res_none = dosage_lookup.mapped_dosages(None)
    assert isinstance(res_none, dosage_lookup.DosageMappingResult)
    assert res_none.freetext_mapped is False
    assert res_none.mapped_indices == frozenset()
    assert len(res_none) == 1

    res_empty = dosage_lookup.mapped_dosages([])
    assert isinstance(res_empty, dosage_lookup.DosageMappingResult)
    assert res_empty.freetext_mapped is False
    assert res_empty.mapped_indices == frozenset()
    assert len(res_empty) == 1
