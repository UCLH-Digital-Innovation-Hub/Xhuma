import pytest
from fhirclient.models import bundle
from app.ccda import fhir2ccda


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


@pytest.mark.asyncio
async def test_usual_name_selected_over_official():
    names = [
        {"use": "official", "family": "OfficialFamily", "given": ["OfficialGiven"]},
        {"use": "usual", "family": "UsualFamily", "given": ["UsualGiven"]},
    ]
    b = bundle.Bundle(create_bundle_dict(names))
    index = {"Organization/1": b.entry[1].resource}

    result = await fhir2ccda.convert_bundle(b, index)
    name_dict = result["ClinicalDocument"]["recordTarget"]["patientRole"]["patient"]["name"]
    assert name_dict["family"]["#text"] == "UsualFamily"
    assert name_dict["given"]["#text"] == "UsualGiven"


@pytest.mark.asyncio
async def test_usual_name_missing_raises_error():
    names = [{"use": "official", "family": "OfficialFamily", "given": ["OfficialGiven"]}]
    b = bundle.Bundle(create_bundle_dict(names))
    index = {"Organization/1": b.entry[1].resource}

    with pytest.raises(ValueError, match="Patient record missing required 'usual' name component"):
        await fhir2ccda.convert_bundle(b, index)
