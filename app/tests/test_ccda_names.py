import xml.etree.ElementTree as ET

import pytest
from fhirclient.models import bundle

from app.ccda import fhir2ccda
from app.ccda.helpers import select_patient_name
from app.soap.responses.iti_47 import iti_47_response
from app.soap.responses.iti_55 import iti_55_response


def create_bundle_dict(names):
    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "entry": [
            {
                "resource": {
                    "resourceType": "Patient",
                    "id": "1",
                    "name": names,
                    "identifier": [{"value": "12345"}],
                    "birthDate": "1990-01-01",
                    "managingOrganization": {"reference": "Organization/1"},
                }
            },
            {
                "resource": {
                    "resourceType": "Organization",
                    "id": "1",
                    "name": "Test Org",
                    "identifier": [{"system": "sys", "value": "val"}],
                    "address": [{"line": ["123 Fake St"], "city": "City", "postalCode": "12345"}],
                }
            },
        ],
    }


@pytest.mark.parametrize(
    "uses, expected_index",
    [
        (["official", "usual"], 1),
        (["nickname", "official"], 1),
        (["official"], 0),
        (["nickname", None], 0),
        ([None, None], 0),
        (["usual", "usual"], 0),
        (["official", "official"], 0),
    ],
)
@pytest.mark.asyncio
async def test_patient_name_priority_across_ccda_and_soap(uses, expected_index):
    names = [
        {"family": f"Family{i}", "given": [f"Given{i}"], **({"use": use} if use else {})} for i, use in enumerate(uses)
    ]
    b = bundle.Bundle(create_bundle_dict(names))
    index = {"Organization/1": b.entry[1].resource}
    result = await fhir2ccda.convert_bundle(b, index)
    name_dict = result["ClinicalDocument"]["recordTarget"]["patientRole"]["patient"]["name"]
    assert name_dict["family"]["#text"] == names[expected_index]["family"]
    assert name_dict["given"]["#text"] == names[expected_index]["given"][0]

    patient = {
        "id": "12345",
        "name": names,
        "gender": "female",
        "birthDate": "1990-01-01",
        "address": [{"line": ["123 Fake St"], "postalCode": "12345"}],
        "generalPractitioner": [{"identifier": {"value": "TEST"}}],
    }
    query = {"queryId": {"@root": "test"}}
    responses = [
        await iti_55_response("message", patient, query),
        await iti_47_response("message", patient, "ceid", query),
    ]
    for response in responses:
        root = ET.fromstring(response)
        name = root.find(".//{urn:hl7-org:v3}patientPerson/{urn:hl7-org:v3}name")
        assert name.find("{urn:hl7-org:v3}family").text == names[expected_index]["family"]
        assert name.find("{urn:hl7-org:v3}given").text == names[expected_index]["given"][0]


@pytest.mark.parametrize("names", [None, []])
def test_missing_names_raise_explicit_error(names):
    with pytest.raises(ValueError, match="Patient record contains no names"):
        select_patient_name(names)
