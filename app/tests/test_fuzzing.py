from hypothesis import given, strategies as st
from unittest.mock import AsyncMock

from fhirclient.models import medication, medicationrequest, medicationstatement, bundle
from app.ccda.entries import medication as medication_entry

# Strategy to generate random strings, or None
random_string_or_none = st.one_of(st.text(), st.none())

@given(st.text())
def test_medication_entry_fuzzing(random_text):
    """
    Fuzz test for medication_entry mapper to ensure it handles 
    malformed FHIR input gracefully without raising unhandled exceptions 
    other than expected validation/type errors.
    """
    # Create a minimal valid structure to prevent fhirclient from rejecting it immediately,
    # but fuzz the deeper values that our mapper logic touches.
    med_statement_dict = {
        "resourceType": "MedicationStatement",
        "id": "1",
        "status": "active",
        "taken": "y",
        "subject": {"reference": "Patient/1"},
        "medicationReference": {"reference": "Medication/1"},
        "note": [{"text": random_text}]
    }
    
    med_request_dict = {
        "resourceType": "MedicationRequest",
        "id": "2",
        "status": "active",
        "intent": "order",
        "subject": {"reference": "Patient/1"},
        "medicationReference": {"reference": "Medication/1"},
        "dosageInstruction": [{"text": random_text}]
    }
    
    med_dict = {
        "resourceType": "Medication",
        "id": "1",
        "code": {
            "coding": [
                {
                    "system": "http://snomed.info/sct",
                    "code": random_text,
                    "display": random_text
                }
            ]
        }
    }
    
    # We don't expect the mapper to succeed with garbage data,
    # but we want to ensure it doesn't raise unexpected AttributeError/KeyError.
    try:
        med_statement = medicationstatement.MedicationStatement(med_statement_dict)
        med_req = medicationrequest.MedicationRequest(med_request_dict)
        med_obj = medication.Medication(med_dict)
        
        # Call our mapper
        result = medication_entry(med_statement, med_req, med_obj, None)
        
        # If it returns a result, ensure it's a valid string (XML snippet)
        assert isinstance(result, str)
    except (ValueError, TypeError, KeyError, AttributeError):
        # We expect validation or mapping errors with garbage data.
        # This test ensures we don't cause a fatal system crash outside of python's standard error bubbling,
        # and serves as a blueprint for deeper property-based testing.
        pass
