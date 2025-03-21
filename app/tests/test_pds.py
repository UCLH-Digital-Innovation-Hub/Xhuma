from unittest.mock import patch

import pytest
from fhirclient.models import patient as p

from app.pds.pds import lookup_patient


@pytest.fixture
def mock_response():
    with patch("httpx.get") as mock_get:
        yield mock_get


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
