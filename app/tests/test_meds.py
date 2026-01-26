import json
import pprint
from unittest.mock import patch

from fhirclient.models import bundle
from fhirclient.models import list as fhirlist
from fhirclient.models import medication, medicationstatement

from app.ccda.entries import medication as medication_entry
from app.ccda.models.base import SubstanceAdministration
from app.ccda.models.datatypes import II

med = medication.Medication(
    {
        "resourceType": "Medication",
        "id": "21",
        "meta": {
            "profile": [
                "https://fhir.nhs.uk/STU3/StructureDefinition/CareConnect-GPC-Medication-1"
            ]
        },
        "code": {
            "coding": [
                {
                    "system": "http://snomed.info/sct",
                    "code": "411533003",
                    "display": "Metformin 2G Modified-Release tablets",
                },
                {
                    "system": "https://fhir.hl7.org.uk/Id/multilex-drug-codes",
                    "code": "16967001",
                    "display": "Metformin 1g modified release tablets",
                    "userSelected": True,
                },
            ]
        },
    }
)

med_statement = medicationstatement.MedicationStatement(
    {
        "resourceType": "MedicationStatement",
        "id": "9",
        "meta": {
            "profile": [
                "https://fhir.nhs.uk/STU3/StructureDefinition/CareConnect-GPC-MedicationStatement-1"
            ]
        },
        "extension": [
            {
                "url": "https://fhir.nhs.uk/STU3/StructureDefinition/Extension-CareConnect-GPC-MedicationStatementLastIssueDate-1",
                "valueDateTime": "2025-02-28",
            },
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
                "url": "https://fhir.hl7.org.uk/STU3/StructureDefinition/Extension-CareConnect-MedicationStatementDosageLastChanged-1",
                "valueDateTime": "2025-02-28T00:00:00+00:00",
            },
        ],
        "identifier": [
            {
                "system": "https://fhir.nhs.uk/Id/cross-care-setting-identifier",
                "value": "398c49aa-1933-11f0-b9fc-00505692d4aa",
            }
        ],
        "basedOn": [{"reference": "MedicationRequest/32"}],
        "status": "active",
        "medicationReference": {"reference": "Medication/21"},
        "effectivePeriod": {
            "start": "2024-05-22T00:00:00+01:00",
            "end": "2025-03-26T00:00:00+00:00",
        },
        "dateAsserted": "2024-05-22T00:00:00+01:00",
        "subject": {"reference": "Patient/2"},
        "taken": "unk",
        "dosage": [
            {"text": "2 tablets a day", "patientInstruction": "With evening meal"}
        ],
    }
)
structured_med = medication.Medication(
    {
        "resourceType": "Medication",
        "id": "A37EA2D2-69D6-43C9-BB6F-66CF8D9D50F7",
        "meta": {
            "profile": [
                "https://fhir.nhs.uk/STU3/StructureDefinition/CareConnect-GPC-Medication-1"
            ]
        },
        "code": {
            "coding": [
                {
                    "system": "https://fhir.hl7.org.uk/Id/emis-drug-codes",
                    "code": "LAOR14898NEMIS",
                    "display": "Lansoprazole 15mg orodispersible tablets",
                    "userSelected": True,
                },
                {
                    "system": "http://snomed.info/sct",
                    "code": "4053411000001103",
                    "display": "Lansoprazole 15mg orodispersible tablets",
                },
            ]
        },
    }
)
sturctured_statement = medicationstatement.MedicationStatement(
    {
        "resourceType": "MedicationStatement",
        "id": "2E352BA6-8F87-479B-BC80-41494027F2E6-MS",
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
            }
        ],
        "identifier": [
            {
                "system": "https://EMISWeb/A82038",
                "value": "7DC1C5D8540B4A7C8E19CBD3426A8CC62E352BA68F87479BBC8041494027F2E6MS",
            }
        ],
        "basedOn": [
            {"reference": "MedicationRequest/2E352BA6-8F87-479B-BC80-41494027F2E6"}
        ],
        "status": "active",
        "medicationReference": {
            "reference": "Medication/A37EA2D2-69D6-43C9-BB6F-66CF8D9D50F7"
        },
        "effectivePeriod": {"start": "2020-03-04"},
        "dateAsserted": "2020-03-04T16:35:02.273+00:00",
        "subject": {"reference": "Patient/37"},
        "taken": "unk",
        "note": [{"text": "Patient Notes:Take 30 mins before a meal or snack"}],
        "dosage": [
            {
                "text": "1 tablet, daily, in morning, 30 minutes before a meal",
                "timing": {
                    "repeat": {
                        "frequency": 1,
                        "period": 1,
                        "periodUnit": "d",
                        "when": ["MORN", "AC"],
                        "offset": 30,
                    }
                },
                "doseQuantity": {
                    "value": 1,
                    "unit": "tablet",
                    "system": "http://snomed.info/sct",
                    "code": "428673006",
                },
            }
        ],
    }
)


# write tests to check if the pydantic models are working correctly
# @patch("app.ccda.entries.medication.referenced_med", return_value=med)
def test_substance_administration():
    """
    Test the SubstanceAdministration model
    """

    index_dict = {
        "Medication/21": med,
        "MedicationStatement/9": med,
    }
    substance_administration = medication_entry(med_statement, index_dict)
    substance_administration = substance_administration["substanceAdministration"]
    # print(substance_administration)

    assert substance_administration["@classCode"] == "SBADM"
    assert substance_administration["@moodCode"] == "INT"
    assert len(substance_administration["id"]) == 1
    # assert effective time list contains low
    assert substance_administration["effectiveTime"][0]["low"]["@value"] == "20240522"
    # assert substance_administration.id[0].root is not None


def test_structured_dosage():
    """
    Test the structured dosage
    """
    with open("app/tests/fixtures/bundles/9690937472.json", "r") as f:
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
    medication_list = []
    for list in fhir_bundle.entry:
        if isinstance(list.resource, fhirlist.List):
            # print(entry.resource.title)
            if list.resource.title == "Medications and medical devices":
                # pprint.pprint(list.resource.as_json())
                for entry in list.resource.entry:
                    # pprint.pprint(entry.as_json())
                    referenced_item = bundle_index[entry.item.reference]
                    # pprint.pprint(referenced_item.as_json())

                    entry_data, tablerow = medication_entry(
                        referenced_item,
                        bundle_index,
                    )
                    medication_list.append(entry_data)

                    assert (
                        entry_data["substanceAdministration"]["@classCode"] == "SBADM"
                    )
    # print(medication_list)
    assert len(medication_list) == 27


def test_structured_detail():
    index_dict = {
        "Medication/A37EA2D2-69D6-43C9-BB6F-66CF8D9D50F7": structured_med,
        "MedicationStatement/9": structured_med,
    }
    substance_administration = medication_entry(structured_statement, index_dict)
    substance_administration = substance_administration.entry
    substance_administration = substance_administration["substanceAdministration"]

    pprint.pprint(substance_administration)
    assert substance_administration["@classCode"] == "SBADM"
    assert substance_administration["@moodCode"] == "INT"
    # assert (
    #     substance_administration["id"][0]["@extension"]
    #     == "https://EMISWeb/A82038/7DC1C5D8540B4A7C8E19CBD3426A8CC62E352BA68F87479BBC8041494027F2E6MS"
    # )
    assert substance_administration["effectiveTime"][0]["low"]["@value"] == "20200304"
    assert substance_administration["effectiveTime"][1]["@xsi:type"] == "PIVL_TS"
    assert (
        substance_administration["effectiveTime"][1]["@institutionSpecified"] == "true"
    )
    assert substance_administration["effectiveTime"][1]["period"]["@value"] == 1.0
    assert substance_administration["effectiveTime"][1]["period"]["@unit"] == "d"
    assert substance_administration["doseQuantity"]["@xsi:type"] == "PQ"
    assert substance_administration["doseQuantity"]["translation"]["@value"] == 1
    assert (
        substance_administration["doseQuantity"]["translation"]["originalText"]
        == "tablet"
    )
    assert (
        substance_administration["doseQuantity"]["translation"]["@codeSystem"]
        == "2.16.840.1.113883.6.96"
    )


new_structured_statement = medicationstatement.MedicationStatement(
    {
        "resourceType": "MedicationStatement",
        "id": "A07283C9-A77A-4850-8092-9AB8486D2865-MS",
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
                "valueDateTime": "2026-01-21T00:00:00+00:00",
            },
        ],
        "identifier": [
            {
                "system": "https://EMISWeb/A82038",
                "value": "AB6A0197A1E441E4998E410F2CF2DE43A07283C9A77A485080929AB8486D2865MS",
            }
        ],
        "basedOn": [
            {"reference": "MedicationRequest/A07283C9-A77A-4850-8092-9AB8486D2865"}
        ],
        "status": "completed",
        "medicationReference": {
            "reference": "Medication/C60BB8CF-14D7-46F7-83A7-34007026F45E"
        },
        "effectivePeriod": {"start": "2026-01-21", "end": "2026-02-18"},
        "dateAsserted": "2026-01-21T15:22:36.637+00:00",
        "subject": {"reference": "Patient/AB6A0197-A1E4-41E4-998E-410F2CF2DE43"},
        "taken": "unk",
        "note": [
            {"text": "Patient Notes:In addition to your furosemide"},
            {"text": "Patient Notes:Issue number 1 In addition to your furosemide"},
        ],
        "dosage": [
            {
                "text": "One To Be Taken Daily",
                "patientInstruction": "In addition to your furosemide",
                "timing": {"repeat": {"frequency": 1, "period": 1, "periodUnit": "d"}},
                "method": {
                    "coding": [
                        {
                            "system": "http://snomed.info/sct",
                            "code": "419652001",
                            "display": "Take",
                        }
                    ]
                },
                "doseQuantity": {"value": 1},
            }
        ],
    }
)

new_med = medication.Medication(
    {
        "resourceType": "Medication",
        "id": "C60BB8CF-14D7-46F7-83A7-34007026F45E",
        "meta": {
            "profile": [
                "https://fhir.nhs.uk/STU3/StructureDefinition/CareConnect-GPC-Medication-1"
            ]
        },
        "code": {
            "coding": [
                {
                    "system": "https://fhir.hl7.org.uk/Id/emis-drug-codes",
                    "code": "ATTA230",
                    "display": "Atenolol 100mg tablets",
                    "userSelected": True,
                },
                {
                    "system": "http://snomed.info/sct",
                    "code": "42370411000001101",
                    "display": "Atenolol 100mg tablets",
                },
            ]
        },
    }
)


def test_new_structured_detail():
    index_dict = {
        "Medication/C60BB8CF-14D7-46F7-83A7-34007026F45E": new_med,
        "MedicationStatement/A07283C9-A77A-4850-8092-9AB8486D2865-MS": new_structured_statement,
    }
    substance_administration = medication_entry(new_structured_statement, index_dict)
    substance_administration = substance_administration["substanceAdministration"]

    pprint.pprint(substance_administration)
    assert substance_administration["@classCode"] == "SBADM"
    assert substance_administration["@moodCode"] == "INT"
    assert substance_administration["doseQuantity"]["@xsi:type"] == "PQ"
    assert substance_administration["doseQuantity"]["@value"] == 1
    # assert quantiy unit is not present
    assert "@unit" not in substance_administration["doseQuantity"]
