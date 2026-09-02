import pytest
from app.ccda.fhir2ccda import convert_bundle
from app.ccda.entries import result, FHIRValidationError
from fhirclient.models.bundle import Bundle
from fhirclient.models.observation import Observation
from fhirclient.models.organization import Organization


@pytest.mark.asyncio
async def test_telecom_and_gender_mapping():
    bundle_dict = {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [
            {
                "resource": {
                    "resourceType": "Organization",
                    "id": "2",
                    "identifier": [
                        {
                            "system": "https://fhir.nhs.uk/Id/ods-organization-code",
                            "value": "GP123",
                        }
                    ],
                    "name": "Test GP Practice",
                    "address": [
                        {
                            "line": ["123 Fake St"],
                            "city": "London",
                            "postalCode": "W1 1AA",
                        }
                    ],
                }
            },
            {
                "resource": {
                    "resourceType": "Patient",
                    "id": "1",
                    "identifier": [
                        {
                            "system": "https://fhir.nhs.uk/Id/nhs-number",
                            "value": "1234567890",
                        }
                    ],
                    "name": [{"family": "Smith", "given": ["John"], "use": "official"}],
                    "gender": "unknown",  # should map to UNK
                    "birthDate": "1980-01-01",
                    "managingOrganization": {"reference": "Organization/2"},
                    "telecom": [
                        {"system": "phone", "value": "111", "use": "home"},
                        {"system": "phone", "value": "222", "use": "work"},
                        {"system": "phone", "value": "333", "use": "mobile"},
                        {"system": "email", "value": "test@test.com"},  # no use
                    ],
                }
            },
        ],
    }
    b = Bundle(bundle_dict)
    index = {"Organization/2": b.entry[0].resource}

    res = await convert_bundle(b, index)

    # Check GP disclaimer order independence
    assert (
        "Registered GP: Test GP Practice"
        in res["ClinicalDocument"]["component"]["structuredBody"]["component"][0][
            "section"
        ]["text"]["#text"]
    )

    patient = res["ClinicalDocument"]["recordTarget"]["patientRole"]["patient"]
    # Check gender mapping
    assert patient["administrativeGenderCode"]["@nullFlavor"] == "UNK"
    assert "@code" not in patient["administrativeGenderCode"]

    # Check telecom
    telecoms = res["ClinicalDocument"]["recordTarget"]["patientRole"]["telecom"]
    assert telecoms[0]["@use"] == "HP"
    assert telecoms[1]["@use"] == "WP"
    assert telecoms[2]["@use"] == "MC"
    assert "@use" not in telecoms[3]


def test_result_observation_status():
    obs_dict = {
        "resourceType": "Observation",
        "id": "obs1",
        "status": "final",
        "code": {"coding": [{"system": "http://snomed.info/sct", "code": "123"}]},
        "valueQuantity": {"value": 1.0, "unit": "mg"},
        "issued": "2023-01-01T00:00:00Z",
    }

    org_dict = {
        "resourceType": "Observation",
        "id": "org1",
        "status": "preliminary",
        "code": {"coding": [{"system": "http://snomed.info/sct", "code": "456"}]},
        "performer": [{"reference": "Organization/1"}],
        "identifier": [{"system": "sys", "value": "val"}],
        "related": [
            {"type": "has-member", "target": {"reference": "Observation/obs1"}}
        ],
    }

    obs = Observation(obs_dict)
    org = Observation(org_dict)

    performer_org = Organization(
        {
            "resourceType": "Organization",
            "id": "1",
            "identifier": [{"system": "sys", "value": "val"}],
        }
    )

    index = {"Observation/obs1": obs, "Organization/1": performer_org}

    res = result(org, index)

    # ResultsOrganizer should be active (preliminary -> active)
    assert res["statusCode"]["@code"] == "active"

    # ResultObservation should be completed (final -> completed)
    # The component is a list of Observation objects which in the dict are often under an 'observation' key, let's just search the component
    comp = res["component"][0]
    if "observation" in comp:
        assert comp["observation"]["statusCode"]["@code"] == "completed"
    else:
        assert comp["statusCode"]["@code"] == "completed"

    obs_dict_unknown = obs_dict.copy()
    obs_dict_unknown["status"] = "unknown-status"
    index_unknown = {
        "Observation/obs1": Observation(obs_dict_unknown),
        "Organization/1": performer_org,
    }
    with pytest.raises(FHIRValidationError):
        result(org, index_unknown)


def test_result_reference_range():
    org_dict = {
        "resourceType": "Observation",
        "id": "org1",
        "status": "final",
        "code": {"coding": [{"system": "http://snomed.info/sct", "code": "456"}]},
        "performer": [{"reference": "Organization/1"}],
        "identifier": [{"system": "sys", "value": "val"}],
        "related": [
            {"type": "has-member", "target": {"reference": "Observation/obs1"}}
        ],
    }

    obs_dict = {
        "resourceType": "Observation",
        "id": "obs1",
        "status": "final",
        "code": {"coding": [{"system": "http://snomed.info/sct", "code": "123"}]},
        "valueQuantity": {"value": 1.0, "unit": "mg"},
        "referenceRange": [
            {"low": {"value": 0.5, "unit": "mg"}, "high": {"value": 2.0, "unit": "mg"}},
            {
                "low": {"value": 3.0}  # uses fallback unit
            },
            {
                "high": {"value": 5.0}  # uses fallback unit
            },
            {"text": "Normal range"},
        ],
    }

    org = Observation(org_dict)
    obs = Observation(obs_dict)
    performer_org = Organization(
        {
            "resourceType": "Organization",
            "id": "1",
            "identifier": [{"system": "sys", "value": "val"}],
        }
    )
    index = {"Observation/obs1": obs, "Organization/1": performer_org}

    res = result(org, index)

    comp = res["component"][0]
    ranges = comp.get("observation", comp).get("referenceRange", [])
    assert len(ranges) == 4

    # 1. both bounds
    range1 = ranges[0]["observationRange"]["value"]
    assert range1["low"]["@value"] == 0.5
    assert range1["low"]["@unit"] == "mg"
    assert range1["high"]["@value"] == 2.0

    # 2. low only
    range2 = ranges[1]["observationRange"]["value"]
    assert range2["low"]["@value"] == 3.0
    assert range2["low"]["@unit"] == "mg"  # fallback
    assert "high" not in range2

    # 3. high only
    range3 = ranges[2]["observationRange"]["value"]
    assert range3["high"]["@value"] == 5.0
    assert range3["high"]["@unit"] == "mg"  # fallback
    assert "low" not in range3

    # 4. text only
    range4 = ranges[3]["observationRange"]
    assert range4["text"] == "Normal range"
    assert "value" not in range4
