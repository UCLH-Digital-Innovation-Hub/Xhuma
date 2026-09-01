import json
import pytest
from app.fhir.medications import MedicationStatement, MedicationRequest, Medication


def test_parse_gp_connect_medication():
    """
    Test that our bespoke Pydantic models correctly parse and validate 
    real NHS GP Connect Medication resources, including complex dosage structures.
    """
    with open("app/tests/fixtures/bundles/9692136744.json", "r") as f:
        bundle = json.load(f)
        
    med_statements = [
        e["resource"] for e in bundle.get("entry", []) 
        if e.get("resource", {}).get("resourceType") == "MedicationStatement"
    ]
    med_requests = [
        e["resource"] for e in bundle.get("entry", []) 
        if e.get("resource", {}).get("resourceType") == "MedicationRequest"
    ]
    meds = [
        e["resource"] for e in bundle.get("entry", []) 
        if e.get("resource", {}).get("resourceType") == "Medication"
    ]
    
    assert len(med_statements) > 0, "Expected to find MedicationStatement resources in test bundle"
    assert len(med_requests) > 0, "Expected to find MedicationRequest resources in test bundle"
    assert len(meds) > 0, "Expected to find Medication resources in test bundle"
    
    for ms_data in med_statements:
        ms = MedicationStatement.model_validate(ms_data)
        assert ms.resourceType == "MedicationStatement"
        if ms.dosage:
            # Test complex dosage parsing
            for d in ms.dosage:
                if d.timing and d.timing.repeat:
                    assert hasattr(d.timing.repeat, "frequency")
    
    for mr_data in med_requests:
        mr = MedicationRequest.model_validate(mr_data)
        assert mr.resourceType == "MedicationRequest"
        
    for m_data in meds:
        m = Medication.model_validate(m_data)
        assert m.resourceType == "Medication"
        assert len(m.code.coding) > 0
        has_snomed = any(c.system == "http://snomed.info/sct" for c in m.code.coding)
        assert has_snomed, "SCAL requires a SNOMED code for Medication"
