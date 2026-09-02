import pytest
import xmltodict
import defusedxml.ElementTree as ET
from app.ccda.fhir2ccda import convert_bundle
import json

@pytest.mark.asyncio
async def test_xml_serialization_no_xsi_type_any():
    from fhirclient.models.bundle import Bundle
    with open("app/tests/fixtures/bundles/9690937278.json", "r") as f:
        bundle_dict = json.load(f)
        
    b = Bundle(bundle_dict)
    index = {}
    for entry in getattr(b, "entry", []):
        if hasattr(entry, "resource") and getattr(entry.resource, "id", None):
            res = entry.resource
            index[f"{res.resource_type}/{res.id}"] = res
        
    # convert_bundle should take the parsed dict and return a CCDA dict
    ccda_dict = await convert_bundle(b, index)
    
    xml = xmltodict.unparse(ccda_dict)
    
    # Assert it parses back into valid XML
    root = ET.fromstring(xml)
    
    # Check for leaked xsi:type="ANY"
    assert "xsi:type=\"ANY\"" not in xml
    assert "resource_type" not in xml
    
    # Optionally assert something about PRN preconditions or Observations
