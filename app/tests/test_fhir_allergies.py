import json
import pytest
from app.fhir.allergies import AllergyIntolerance


def test_parse_gp_connect_allergy():
    """
    Test that our bespoke Pydantic models correctly parse and validate 
    real NHS GP Connect AllergyIntolerance resources.
    """
    with open("app/tests/fixtures/bundles/9692136744.json", "r") as f:
        bundle = json.load(f)
        
    allergies = [
        e["resource"] 
        for e in bundle.get("entry", []) 
        if e.get("resource", {}).get("resourceType") == "AllergyIntolerance"
    ]
    
    assert len(allergies) > 0, "Expected to find AllergyIntolerance resources in test bundle"
    
    for allergy_data in allergies:
        # This will trigger our @field_validator which enforces the SCAL requirements
        allergy = AllergyIntolerance.model_validate(allergy_data)
        
        assert allergy.resourceType == "AllergyIntolerance"
        assert allergy.assertedDate is not None
        assert allergy.code is not None
        
        # Verify SNOMED SCAL strictness
        assert len(allergy.code.coding) > 0
        has_snomed = any(c.system == "http://snomed.info/sct" for c in allergy.code.coding)
        assert has_snomed, "SCAL requires a SNOMED code for AllergyIntolerance"
        
        # Verify nested manifestations parsed correctly if present
        if allergy.reaction:
            for reaction in allergy.reaction:
                for manifestation in reaction.manifestation:
                    assert len(manifestation.coding) > 0
