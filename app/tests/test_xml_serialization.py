import pytest
import xmltodict
from app.ccda.fhir2ccda import convert_bundle
import json


import os

FIXTURES = [
    "9690937278.json",
    "9690937286.json",
    "9690937472.json",
    "9690938118.json",
    "9692136744.json",
    "9692140466.json",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("fixture_name", FIXTURES)
async def test_xml_serialization_no_xsi_type_any(fixture_name):
    from fhirclient.models.bundle import Bundle

    with open(os.path.join("app/tests/fixtures/bundles", fixture_name), "r") as f:
        bundle_dict = json.load(f)

    def remove_fhir_comments(d):
        if isinstance(d, dict):
            d.pop("fhir_comments", None)
            for v in d.values():
                remove_fhir_comments(v)
        elif isinstance(d, list):
            for i in d:
                remove_fhir_comments(i)

    remove_fhir_comments(bundle_dict)

    b = Bundle(bundle_dict)
    index = {}
    for entry in getattr(b, "entry", None) or []:
        if hasattr(entry, "resource") and getattr(entry.resource, "id", None):
            res = entry.resource
            index[f"{res.resource_type}/{res.id}"] = res

    # convert_bundle should take the parsed dict and return a CCDA dict
    ccda_dict = await convert_bundle(b, index)

    xml = xmltodict.unparse(ccda_dict)

    # Assert it parses back into valid XML without using standard library xml.etree to avoid SAST warnings
    parsed = xmltodict.parse(xml)
    assert parsed is not None

    # Check for leaked xsi:type="ANY"
    assert 'xsi:type="ANY"' not in xml
    assert "resource_type" not in xml

    # Optionally assert something about PRN preconditions or Observations
