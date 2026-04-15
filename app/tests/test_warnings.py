import json
from unittest.mock import patch

import pytest
import xmltodict
from fastapi.testclient import TestClient
from fhirclient.models import bundle
from httpx import Response

from app.ccda import fhir2ccda
from app.main import app
from app.tests.configure_tests import get_nhs_ids, load_bundle, load_pds


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
        except:
            pass

    xml_ccda = await fhir2ccda.convert_bundle(fhir_bundle, bundle_index)
    with open("test_warnings.xml", "w") as output:
        output.write(xmltodict.unparse(xml_ccda, pretty=True))

    # check note is present in the medications section
    medications_section = xml_ccda["ClinicalDocument"]["component"]["structuredBody"][
        "component"
    ]
    medications_section = [
        section
        for section in medications_section
        if section["section"]["code"]["@code"] == "10160-0"
    ][0]
    medications_text = medications_section["section"]["text"]["div"]

    assert any(
        "information not available" in text["p"].lower() for text in medications_text
    )
