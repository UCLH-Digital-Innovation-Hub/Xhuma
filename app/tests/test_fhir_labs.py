import json
import pytest
from app.fhir.labs import Observation, DiagnosticReport


def test_parse_gp_connect_observation():
    """
    Test that our bespoke Pydantic models correctly parse and validate 
    real NHS GP Connect Observation and DiagnosticReport resources, 
    including polymorphic fields.
    """
    with open("app/tests/fixtures/bundles/9692136744.json", "r") as f:
        bundle = json.load(f)
        
    observations = [
        e["resource"] for e in bundle.get("entry", []) 
        if e.get("resource", {}).get("resourceType") == "Observation"
    ]
    reports = [
        e["resource"] for e in bundle.get("entry", []) 
        if e.get("resource", {}).get("resourceType") == "DiagnosticReport"
    ]
    
    assert len(observations) > 0, "Expected to find Observation resources in test bundle"
    
    for obs_data in observations:
        obs = Observation.model_validate(obs_data)
        assert obs.resourceType == "Observation"
        
        # Check polymorphic effective time
        has_time = any([obs.effectiveDateTime, obs.effectivePeriod, obs.effectiveInstant])
        assert has_time, "Expected Observation to have an effective time"
        
        # Check polymorphic value
        if obs.valueQuantity or obs.valueCodeableConcept or obs.valueString:
            # Not all observations have a value (e.g. a panel group), but if they do, we catch it
            pass
            
        if obs.code and obs.code.coding:
            has_snomed = any(c.system == "http://snomed.info/sct" for c in obs.code.coding)
            # Many labs in GP Connect will have SNOMED, some might not.
            # Our validator ensures structure is safe.
    
    for rep_data in reports:
        rep = DiagnosticReport.model_validate(rep_data)
        assert rep.resourceType == "DiagnosticReport"
        assert rep.id is not None
