import pytest
from fhirclient.models import medication, medicationrequest, medicationstatement

from app.ccda.entries import medication as medication_entry

prn_statement = medicationstatement.MedicationStatement(
    {
        "resourceType": "MedicationStatement",
        "id": "968546F0-EF03-491B-A045-4D46EE61A860-MS",
        "meta": {
            "profile": [
                "https://fhir.nhs.uk/STU3/StructureDefinition/CareConnect-GPC-MedicationStatement-1"
            ]
        },
        "extension": [
            {
                "url": "https://fhir.nhs.uk/STU3/StructureDefinition/Extension-CareConnect-GPC-PrescribingAgency-1",
                "valueCodeableConcept": {
                    "coding": [
                        {
                            "system": "https://fhir.nhs.uk/STU3/CodeSystem/CareConnect-PrescribingAgency-1",
                            "code": "prescribed-at-gp-practice",
                            "display": "Prescribed at GP practice",
                        }
                    ]
                },
            },
            {
                "url": "https://fhir.nhs.uk/STU3/StructureDefinition/Extension-CareConnect-GPC-MedicationStatementLastIssueDate-1",
                "valueDateTime": "2026-02-24T00:00:00+00:00",
            },
        ],
        "identifier": [
            {
                "system": "https://EMISWeb/A82038",
                "value": "593C97B57B9943269140B329CC03A0D1968546F0EF03491BA0454D46EE61A860MS",
            }
        ],
        "basedOn": [
            {"reference": "MedicationRequest/968546F0-EF03-491B-A045-4D46EE61A860"}
        ],
        "status": "completed",
        "medicationReference": {
            "reference": "Medication/12EE2DA3-065A-41CD-93A3-67A80785C511"
        },
        "effectivePeriod": {"start": "2026-02-24", "end": "2026-03-10"},
        "dateAsserted": "2026-02-24T10:08:32.33+00:00",
        "subject": {"reference": "Patient/593C97B5-7B99-4326-9140-B329CC03A0D1"},
        "taken": "unk",
        "dosage": [
            {
                "text": "Take One Tablet As Required On Each Day There Is A Risk Of Drinking Alcohol. Maximum One Tablet Daily.",
                "additionalInstruction": [
                    {"text": "on each day there is a risk of drinking alcohol"}
                ],
                "asNeededBoolean": True,
                "method": {
                    "coding": [
                        {
                            "system": "http://snomed.info/sct",
                            "code": "419652001",
                            "display": "Oral",
                        }
                    ]
                },
                "doseQuantity": {
                    "value": 1,
                    "unit": "mg",
                    "system": "http://snomed.info/sct",
                    "code": "428673006",
                },
                "maxDosePerPeriod": {
                    "numerator": {
                        "value": 1,
                        "unit": "Tablet",
                        "system": "http://snomed.info/sct",
                        "code": "428673006",
                    },
                    "denominator": {
                        "value": 1,
                        "unit": "day",
                        "system": "http://unitsofmeasure.org",
                        "code": "d",
                    },
                },
            }
        ],
    }
)

prn_med = medication.Medication(
    {
        "resourceType": "Medication",
        "id": "1004837_1",
        "meta": {
            "profile": [
                "https://fhir.nhs.uk/STU3/StructureDefinition/CareConnect-GPC-Medication-1"
            ]
        },
        "code": {
            "coding": [
                {
                    "system": "http://snomed.info/sct",
                    "code": "1122411000001107",
                    "display": "Timolol 0.25% eye drops 5 ml",
                }
            ],
            "text": "Timolol 0.25% eye drops",
        },
    }
)

med_request = medicationrequest.MedicationRequest(
    {
        "resourceType": "MedicationRequest",
        "id": "968546F0-EF03-491B-A045-4D46EE61A860",
        "meta": {
            "profile": [
                "https://fhir.nhs.uk/STU3/StructureDefinition/CareConnect-GPC-MedicationRequest-1"
            ]
        },
        "extension": [
            {
                "url": "https://fhir.nhs.uk/STU3/StructureDefinition/Extension-CareConnect-GPC-PrescriptionType-1",
                "valueCodeableConcept": {
                    "coding": [
                        {
                            "system": "https://fhir.nhs.uk/STU3/CodeSystem/CareConnect-PrescriptionType-1",
                            "code": "acute",
                            "display": "Acute",
                        }
                    ]
                },
            }
        ],
        "identifier": [
            {
                "system": "https://EMISWeb/A82038",
                "value": "593C97B57B9943269140B329CC03A0D1968546F0EF03491BA0454D46EE61A860",
            }
        ],
        "groupIdentifier": {"value": "968546f0-ef03-491b-a045-4d46ee61a860"},
        "status": "completed",
        "intent": "plan",
        "medicationReference": {
            "reference": "Medication/12EE2DA3-065A-41CD-93A3-67A80785C511"
        },
        "subject": {"reference": "Patient/593C97B5-7B99-4326-9140-B329CC03A0D1"},
        "authoredOn": "2026-02-24T10:08:32.33+00:00",
        "recorder": {"reference": "Practitioner/C8FD0E2C-3124-4C72-AC8D-ABEA65537D1B"},
        "dosageInstruction": [
            {
                "text": "Take One Tablet As Required On Each Day There Is A Risk Of Drinking Alcohol. Maximum One Tablet Daily.",
                "additionalInstruction": [
                    {"text": "on each day there is a risk of drinking alcohol"}
                ],
                "asNeededBoolean": True,
                "method": {
                    "coding": [
                        {
                            "system": "http://snomed.info/sct",
                            "code": "419652001",
                            "display": "Take",
                        }
                    ]
                },
                "doseQuantity": {
                    "value": 1,
                    "unit": "tablet",
                    "system": "http://snomed.info/sct",
                    "code": "428673006",
                },
                "maxDosePerPeriod": {
                    "numerator": {
                        "value": 1,
                        "unit": "tablet",
                        "system": "http://snomed.info/sct",
                        "code": "428673006",
                    },
                    "denominator": {
                        "value": 1,
                        "unit": "day",
                        "system": "http://unitsofmeasure.org",
                        "code": "d",
                    },
                },
            }
        ],
        "dispenseRequest": {
            "validityPeriod": {"start": "2026-02-24", "end": "2026-03-10"},
            "quantity": {"value": 14, "unit": "tablet"},
            "expectedSupplyDuration": {
                "value": 14,
                "unit": "day",
                "system": "http://unitsofmeasure.org",
                "code": "d",
            },
        },
    }
)


@pytest.mark.asyncio
async def test_prn_medication_statement():
    """Test the conversion of a PRN medication statement to a CCDA entry."""
    # Convert the FHIR MedicationStatement to a CCDA entry
    index_dict = {
        "Medication/12EE2DA3-065A-41CD-93A3-67A80785C511": prn_med,
        "prn_medicationStatement/9": prn_med,
        "MedicationRequest/968546F0-EF03-491B-A045-4D46EE61A860": med_request,
    }
    substance_administration = await medication_entry(prn_statement, index_dict)
    substance_administration = substance_administration.entry
    substance_administration = substance_administration["substanceAdministration"]
    # Print the CCDA entry for debugging purposes
    # pprint.pprint(substance_administration)

    # Check that the entry is not None
    assert substance_administration is not None

    # Check that the entry has the expected structure
    assert "precondition" in substance_administration
    assert substance_administration["precondition"][0]["@typeCode"] == "PRCN"
    assert (
        substance_administration["precondition"][0]["criterion"]["templateId"][0]["@root"]
        == "2.16.840.1.113883.10.20.22.4.25"
    )
    assert (
        substance_administration["precondition"][0]["criterion"]["code"]["@code"]
        == "ASSERTION"
    )
    assert (
        substance_administration["precondition"][0]["criterion"]["value"]["@code"]
        == "ASSERTION"
    )
    assert (
        substance_administration["precondition"][0]["criterion"]["value"]["@displayName"]
        == "As Directed"
    )
    # Check the medication details


@pytest.mark.asyncio
async def test_max_dose_quantity():
    """Test that max dose quantity is correctly converted to the CCDA entry."""
    index_dict = {
        "Medication/12EE2DA3-065A-41CD-93A3-67A80785C511": prn_med,
        "prn_medicationStatement/9": prn_med,
        "MedicationRequest/968546F0-EF03-491B-A045-4D46EE61A860": med_request,
    }
    substance_administration = await medication_entry(prn_statement, index_dict)
    substance_administration = substance_administration.entry
    substance_administration = substance_administration["substanceAdministration"]

    assert "maxDoseQuantity" in substance_administration
    assert substance_administration["maxDoseQuantity"]["numerator"]["@value"] == 1
    assert substance_administration["maxDoseQuantity"]["numerator"]["@unit"] == "Tablet"
