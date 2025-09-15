import json
from unittest.mock import AsyncMock, patch

import pytest
from fhirclient.models import patient as p

from app.pds.pds import lookup_patient, lookup_patient_cached

pytest_plugins = ("pytest_asyncio",)


@pytest.fixture
def mock_response():
    with patch("httpx.get") as mock_get:
        yield mock_get


@pytest.mark.asyncio
async def test_get_data_success(mock_response):
    # Successful response
    mock_response.return_value.json.return_value = {
        "address": [
            {
                "extension": [
                    {
                        "extension": [
                            {
                                "url": "type",
                                "valueCoding": {
                                    "code": "PAF",
                                    "system": "https://fhir.hl7.org.uk/CodeSystem/UKCore-AddressKeyType",
                                },
                            },
                            {"url": "value", "valueString": "19343715"},
                        ],
                        "url": "https://fhir.hl7.org.uk/StructureDefinition/Extension-UKCore-AddressKey",
                    }
                ],
                "id": "XUSFS",
                "line": ["268 PIPER KNOWLE ROAD", "STOCKTON-ON-TEES", "CLEVELAND"],
                "period": {"start": "1998-07-04"},
                "postalCode": "TS19 8JP",
                "use": "home",
            }
        ],
        "birthDate": "1938-12-11",
        "extension": [
            {
                "extension": [
                    {
                        "url": "language",
                        "valueCodeableConcept": {
                            "coding": [
                                {
                                    "code": "de",
                                    "display": "German",
                                    "system": "https://fhir.hl7.org.uk/CodeSystem/UKCore-HumanLanguage",
                                    "version": "1.0.0",
                                }
                            ]
                        },
                    },
                    {"url": "interpreterRequired", "valueBoolean": True},
                ],
                "url": "https://fhir.hl7.org.uk/StructureDefinition/Extension-UKCore-NHSCommunication",
            }
        ],
        "gender": "male",
        "generalPractitioner": [
            {
                "id": "BZYbh",
                "identifier": {
                    "period": {"start": "2023-09-29"},
                    "system": "https://fhir.nhs.uk/Id/ods-organization-code",
                    "value": "B83621",
                },
                "type": "Organization",
            }
        ],
        "id": "9690937278",
        "identifier": [
            {
                "extension": [
                    {
                        "url": "https://fhir.hl7.org.uk/StructureDefinition/Extension-UKCore-NHSNumberVerificationStatus",
                        "valueCodeableConcept": {
                            "coding": [
                                {
                                    "code": "01",
                                    "display": "Number present and verified",
                                    "system": "https://fhir.hl7.org.uk/CodeSystem/UKCore-NHSNumberVerificationStatus",
                                    "version": "1.0.0",
                                }
                            ]
                        },
                    }
                ],
                "system": "https://fhir.nhs.uk/Id/nhs-number",
                "value": "9690937278",
            }
        ],
        "meta": {
            "security": [
                {
                    "code": "U",
                    "display": "unrestricted",
                    "system": "http://terminology.hl7.org/CodeSystem/v3-Confidentiality",
                }
            ],
            "versionId": "4",
        },
        "name": [
            {
                "family": "SAMUAL",
                "given": ["Lucien", "Keith"],
                "id": "LVxyI",
                "period": {"start": "1970-08-11"},
                "prefix": ["MR"],
                "use": "usual",
            }
        ],
        "resourceType": "Patient",
        "telecom": [
            {
                "id": "EAFDAA6A",
                "period": {"start": "2024-11-27"},
                "system": "email",
                "use": "home",
                "value": "luclen@gmail.com",
            }
        ],
    }

    patient = await lookup_patient(9690937278)

    assert patient["resourceType"] == "Patient"
    assert patient["id"] == "9690937278"
    assert patient == p.Patient()


# test the cached version


@pytest.mark.asyncio
async def test_lookup_patient_cached_hit():
    redis = AsyncMock()
    nhsno = "9690937278"
    fake_result = {"resourceType": "Patient", "id": nhsno}

    # Simulate cache hit
    redis.get.return_value = json.dumps(fake_result)

    # Call the cached function
    result = await lookup_patient_cached(nhsno, redis=redis)

    assert result == fake_result
    redis.get.assert_called_once()
    redis.set.assert_not_called()  # Shouldn't set anything on cache hit


@pytest.mark.asyncio
@patch("app.pds.pds.lookup_patient")
async def test_lookup_patient_cached_miss(mock_lookup_patient):
    redis = AsyncMock()
    nhsno = "9690937278"
    fake_result = {"resourceType": "Patient", "id": nhsno}

    # Simulate cache miss
    redis.get.return_value = None
    mock_lookup_patient.return_value = fake_result

    # Call the cached function
    result = await lookup_patient_cached(nhsno, redis=redis)

    assert result == fake_result
    redis.get.assert_called_once()
    redis.set.assert_called_once()

    # Check cached value
    cached_value = json.loads(redis.set.call_args[0][1])
    assert cached_value == fake_result

    # Ensure the original function was called
    mock_lookup_patient.assert_called_once_with(nhsno)
