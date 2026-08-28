import pytest
from unittest.mock import AsyncMock

from app.ccda.entries import medication as medication_entry
from app.ccda.entries import immunization_entry as immunization_entry_func
from app.ccda.entries import observation_entry

from fhirclient.models.medicationstatement import MedicationStatement
from fhirclient.models.immunization import Immunization
from fhirclient.models.observation import Observation

def assert_no_br_in_xmltext(obj):
    """
    Recursively search through a dictionary (representing the CDA XML)
    and assert that no 'xmlText' fields contain '<br' (or any HTML tags really).
    """
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == "xmlText" and isinstance(value, str):
                assert "<br" not in value.lower(), (
                    f"Found '<br' tag in xmlText field! "
                    f"Epic's parser will swallow text preceding this tag. "
                    f"Content: {value}"
                )
            else:
                assert_no_br_in_xmltext(value)
    elif isinstance(obj, list):
        for item in obj:
            assert_no_br_in_xmltext(item)


@pytest.mark.asyncio
async def test_medication_entry_xmltext_validation():
    """
    Ensure that when multiple notes/warnings are generated for a medication,
    the structured xmlText uses standard newlines (\n) and NOT <br />.
    """
    from fhirclient.models import medication as fhirmed
    from fhirclient.models import medicationrequest as fhirmedreq
    med_statement = MedicationStatement({
        "resourceType": "MedicationStatement",
        "id": "1",
        "identifier": [{"system": "urn:oid:2.16.840.1.113883.2.1.3.2.4.18.22", "value": "test-uuid"}],
        "status": "active",
        "taken": "unk",
        "subject": {"reference": "Patient/1"},
        "medicationReference": {"reference": "Medication/1"},
        "basedOn": [{"reference": "MedicationRequest/1"}],
        "effectivePeriod": {"start": "2023-01-01T00:00:00Z"},
        "dosage": [{"text": "1 tablet"}],
        "note": [
            {"text": "Patient requested stop"},
            {"text": "Another warning message"}
        ]
    })
    
    mock_med = fhirmed.Medication({
        "resourceType": "Medication",
        "id": "1",
        "code": {"coding": [{"code": "123", "display": "Med"}]}
    })
    
    mock_med_req = fhirmedreq.MedicationRequest({
        "resourceType": "MedicationRequest",
        "id": "1",
        "status": "completed",
        "intent": "order",
        "subject": {"reference": "Patient/1"},
        "medicationReference": {"reference": "Medication/1"}
    })

    entry_with_row = await medication_entry(
        med_statement, 
        {"Medication/1": mock_med, "MedicationRequest/1": mock_med_req}
    )
    cda_dict = entry_with_row.entry
    assert_no_br_in_xmltext(cda_dict)

def test_immunization_entry_xmltext_validation():
    """
    Ensure that when multiple notes are generated for an immunization,
    the structured xmlText uses standard newlines (\n) and NOT <br />.
    """
    imm = Immunization({
        "resourceType": "Immunization",
        "id": "2",
        "status": "completed",
        "notGiven": False,
        "patient": {"reference": "Patient/1"},
        "primarySource": True,
        "vaccineCode": {"coding": [{"code": "123", "display": "Vaccine"}]},
        "note": [
            {"text": "Refused"},
            {"text": "Patient was ill"}
        ]
    })
    
    entry_with_row = immunization_entry_func(imm, {})
    cda_dict = entry_with_row.entry
    assert_no_br_in_xmltext(cda_dict)

def test_observation_entry_xmltext_validation():
    """
    Ensure that when multiple notes are generated for an observation,
    the structured xmlText uses standard newlines (\n) and NOT <br />.
    """
    obs = Observation({
        "resourceType": "Observation",
        "id": "3",
        "status": "final",
        "code": {"coding": [{"code": "456", "display": "Obs"}]},
        "comment": "First note"
    })
    # Observation in STU3 uses `comment` (string) instead of `note`
    # Xhuma handles both due to extensions, but for fhirclient we provide `comment`.
    
    # We will simulate multiple notes by setting .note manually after init to bypass fhirclient validation
    # since Xhuma's code checks `hasattr(entry, "note")`.
    obs.note = [{"text": "Second note"}]
    
    entry_with_row = observation_entry(obs, {}, "Observations")
    cda_dict = entry_with_row.entry
    assert_no_br_in_xmltext(cda_dict)
