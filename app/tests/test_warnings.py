import json
from unittest.mock import AsyncMock, patch

import pytest
from fhirclient.models import (
    bundle,
    medicationrequest,
    medicationstatement,
)
from fhirclient.models import (
    medication as fhirmed,
)

from app.ccda import fhir2ccda
from app.ccda.entries.medication import medication
from app.ccda.models.datatypes import CD
from app.ccda.models.dmd import DMDConcept, VPIProperty


@pytest.mark.asyncio
async def test_warnings_handling():
    with open("app/tests/fixtures/bundles/9690938118.json", "r") as f:
        structured_dosage_bundle = json.load(f)

    fhir_bundle = bundle.Bundle(structured_dosage_bundle)

    # index resources to allow for resolution
    bundle_index = {}
    for entry in fhir_bundle.entry:
        try:
            address = f"{entry.resource.resource_type}/{entry.resource.id}"
            bundle_index[address] = entry.resource
        except Exception:
            pass

    xml_ccda = await fhir2ccda.convert_bundle(fhir_bundle, bundle_index)

    # check note is present in the medications section
    medications_section = xml_ccda["ClinicalDocument"]["component"]["structuredBody"]["component"]

    medications_section = [
        section
        for section in medications_section
        if section.get("section", {}).get("code", {}).get("@code") == "10160-0"
    ][0]

    medications_text = medications_section["section"]["text"]["paragraph"]["#text"]

    assert "information not available" in medications_text.lower()


# --- Medication Warning Aggregation Regression Tests ---


def _build_test_index(medication_code="42370411000001101", display="Test Med", request_notes=None):
    med = fhirmed.Medication(
        {
            "id": "med-1",
            "code": {
                "coding": [
                    {
                        "system": "http://snomed.info/sct",
                        "code": medication_code,
                        "display": display,
                    }
                ]
            },
        }
    )
    req_json = {
        "id": "req-1",
        "status": "active",
        "intent": "order",
        "subject": {"reference": "Patient/1"},
        "medicationReference": {"reference": "Medication/med-1"},
    }
    if request_notes:
        req_json["note"] = [{"text": n} for n in request_notes]
    req = medicationrequest.MedicationRequest(req_json)
    return {
        "Medication/med-1": med,
        "MedicationRequest/req-1": req,
    }


def _build_statement(dosages, notes=None):
    stmt_json = {
        "id": "stmt-1",
        "identifier": [{"system": "https://example.test", "value": "1"}],
        "status": "active",
        "taken": "unk",
        "subject": {"reference": "Patient/1"},
        "medicationReference": {"reference": "Medication/med-1"},
        "basedOn": [{"reference": "MedicationRequest/req-1"}],
        "effectivePeriod": {"start": "2026-10-29"},
        "dosage": dosages,
    }
    if notes:
        stmt_json["note"] = [{"text": n} for n in notes]
    return medicationstatement.MedicationStatement(stmt_json)


def _extract_notes_and_xml(result):
    br_notes = [note.replace("<br />", "").strip() for note in result.row[6]["BR"] if note.strip()]
    cda = result.entry["substanceAdministration"]
    comment_act = next(
        rel["act"]
        for rel in cda.get("entryRelationship", [])
        if rel.get("act", {}).get("code", {}).get("@code") == "48767-8"
    )
    xml_text = comment_act.get("text", {}).get("xmlText", "")
    return br_notes, xml_text


@pytest.mark.asyncio
@patch("app.ccda.entries.medication.dmd_lookup", new_callable=AsyncMock)
async def test_medication_warnings_cumulative_aggregation(mock_dmd_lookup):
    """Regression test: a single medication simultaneously triggers all applicable warning paths.

    1. dm+d mapping/inference warning (route inferred as Oral from Take)
    2. free-sig / freetext dosage mapping warning (narrative mapped via NHS CSV)
    3. multiple dosage instructions warning (len(dosages) > 1)
    Plus original prescriber note preserved in misc_notes in deterministic order.
    """
    mock_dmd_lookup.return_value = DMDConcept(
        concept_id=42370411000001101,
        valueString="Test Med",
        route=CD(code="26643006", codeSystem="2.16.840.1.113883.6.96", displayName="Oral"),
    )
    index = _build_test_index()
    statement = _build_statement(
        dosages=[
            {"text": "one to be taken each day"},
            {"text": "one daily"},
        ],
        notes=["Prescriber clinical note: take after food"],
    )

    result = await medication(statement, index)
    br_notes, xml_text = _extract_notes_and_xml(result)

    dmd_warning = (
        'Xhuma: Route inferred as Oral from dm+d because the GP source value was "Take"; '
        "this may not represent the patient's actual route of administration. Verify during medicines reconciliation."
    )
    freesig_warning = "Xhuma: Free-text dosage instructions automatically mapped"
    multidose_warning = "Xhuma: Multiple dosage instructions found. Use caution when converting dose**"
    prescriber_note = "Prescriber clinical note: take after food"

    # All expected warnings appear simultaneously in the narrative table row
    assert prescriber_note in br_notes
    assert dmd_warning in br_notes
    assert freesig_warning in br_notes
    assert multidose_warning in br_notes

    # All expected warnings appear simultaneously in machine-readable xmlText
    assert prescriber_note in xml_text
    assert dmd_warning in xml_text
    assert freesig_warning in xml_text
    assert multidose_warning in xml_text

    # Verify deterministic ordering: dm+d -> free-sig -> multiple dosage
    idx_note = br_notes.index(prescriber_note)
    idx_dmd = br_notes.index(dmd_warning)
    idx_freesig = br_notes.index(freesig_warning)
    idx_multidose = br_notes.index(multidose_warning)

    assert idx_note < idx_dmd < idx_freesig < idx_multidose


@pytest.mark.asyncio
@patch("app.ccda.entries.medication.dmd_lookup", new_callable=AsyncMock)
async def test_dmd_warning_alone(mock_dmd_lookup):
    """dm+d warning alone appears when dosage is already structured and single."""
    mock_dmd_lookup.return_value = DMDConcept(
        concept_id=42370411000001101,
        valueString="Test Med",
        vpi=VPIProperty(unit="mg", value=50),
    )
    index = _build_test_index()
    statement = _build_statement(
        dosages=[{"doseQuantity": {"value": 1, "unit": "tablet"}}],
    )

    result = await medication(statement, index)
    br_notes, _ = _extract_notes_and_xml(result)

    assert "Xhuma: Dose of 50 mg automatically mapped via dm+d lookup" in br_notes
    assert not any("Free-text" in n for n in br_notes)
    assert not any("Multiple dosage instructions" in n for n in br_notes)


@pytest.mark.asyncio
@patch("app.ccda.entries.medication.dmd_lookup", new_callable=AsyncMock)
async def test_free_sig_warning_alone(mock_dmd_lookup):
    """Free-sig warning alone appears when narrative dosage is mapped without dm+d conversion."""
    mock_dmd_lookup.return_value = DMDConcept(
        concept_id=42370411000001101,
        valueString="Test Med",
    )
    index = _build_test_index()
    statement = _build_statement(
        dosages=[{"text": "ONE twice a day"}],
    )

    result = await medication(statement, index)
    br_notes, _ = _extract_notes_and_xml(result)

    assert "Xhuma: Free-text dosage instructions automatically mapped" in br_notes
    assert not any("automatically mapped via dm+d" in n for n in br_notes)
    assert not any("Multiple dosage instructions" in n for n in br_notes)


@pytest.mark.asyncio
@patch("app.ccda.entries.medication.dmd_lookup", new_callable=AsyncMock)
async def test_multiple_dosage_warning_alone(mock_dmd_lookup):
    """Multiple dosage warning alone appears when multiple structured dosages exist."""
    mock_dmd_lookup.return_value = DMDConcept(
        concept_id=42370411000001101,
        valueString="Test Med",
    )
    index = _build_test_index()
    statement = _build_statement(
        dosages=[
            {"doseQuantity": {"value": 1, "unit": "tablet"}},
            {"doseQuantity": {"value": 2, "unit": "tablet"}},
        ],
    )

    result = await medication(statement, index)
    br_notes, _ = _extract_notes_and_xml(result)

    assert "Xhuma: Multiple dosage instructions found. Use caution when converting dose**" in br_notes
    assert not any("Free-text" in n for n in br_notes)
    assert not any("automatically mapped via dm+d" in n for n in br_notes)


@pytest.mark.asyncio
@patch("app.ccda.entries.medication.dmd_lookup", new_callable=AsyncMock)
async def test_warnings_do_not_suppress_each_other(mock_dmd_lookup):
    """Proves pairwise warning combinations coexist without suppression."""
    # Case A: dm+d dose mapping + free-sig mapping
    mock_dmd_lookup.return_value = DMDConcept(
        concept_id=42370411000001101,
        valueString="Test Med",
        vpi=VPIProperty(unit="mg", value=20),
    )
    index = _build_test_index()
    statement = _build_statement(
        dosages=[{"text": "ONE twice a day"}],
    )
    result = await medication(statement, index)
    br_notes, _ = _extract_notes_and_xml(result)
    assert "Xhuma: Dose of 20 mg automatically mapped via dm+d lookup" in br_notes
    assert "Xhuma: Free-text dosage instructions automatically mapped" in br_notes

    # Case B: multiple dosage + free-sig mapping
    mock_dmd_lookup.return_value = DMDConcept(
        concept_id=42370411000001101,
        valueString="Test Med",
    )
    statement_multi = _build_statement(
        dosages=[{"text": "ONE twice a day"}, {"text": "ONE daily"}],
    )
    result_multi = await medication(statement_multi, index)
    br_notes_multi, _ = _extract_notes_and_xml(result_multi)
    assert "Xhuma: Free-text dosage instructions automatically mapped" in br_notes_multi
    assert "Xhuma: Multiple dosage instructions found. Use caution when converting dose**" in br_notes_multi


@pytest.mark.asyncio
@patch("app.ccda.entries.medication.dmd_lookup", new_callable=AsyncMock)
async def test_misc_notes_not_overwritten(mock_dmd_lookup):
    """Proves existing clinical/prescriber notes are preserved when warnings append."""
    mock_dmd_lookup.return_value = DMDConcept(
        concept_id=42370411000001101,
        valueString="Test Med",
        vpi=VPIProperty(unit="mg", value=10),
    )
    index = _build_test_index(request_notes=["Request level note: monitor liver"])
    statement = _build_statement(
        dosages=[{"text": "ONE twice a day"}],
        notes=["Statement level note: take with meals"],
    )

    result = await medication(statement, index)
    br_notes, xml_text = _extract_notes_and_xml(result)

    assert "Request level note: monitor liver" in br_notes
    assert "Statement level note: take with meals" in br_notes
    assert "Xhuma: Dose of 10 mg automatically mapped via dm+d lookup" in br_notes
    assert "Xhuma: Free-text dosage instructions automatically mapped" in br_notes

    assert "Request level note: monitor liver" in xml_text
    assert "Statement level note: take with meals" in xml_text
