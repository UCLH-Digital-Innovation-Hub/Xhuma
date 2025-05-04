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
    print(substance_administration)

    assert substance_administration["@classCode"] == "SBADM"
    assert substance_administration["@moodCode"] == "INT"
    assert substance_administration["code"]["@codeSystem"] == "2.16.840.1.113883.5.6"
    assert len(substance_administration["id"]) == 1
    # assert effective time list contains low
    assert substance_administration["effectiveTime"]["low"]["@value"] == "20240522"
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

                    entry_data = medication_entry(
                        referenced_item,
                        bundle_index,
                    )
                    medication_list.append(entry_data)

                    assert (
                        entry_data["substanceAdministration"]["@classCode"] == "SBADM"
                    )
                    assert (
                        entry_data["substanceAdministration"]["entryRelationship"][0][
                            "observation"
                        ]["text"]
                        is not None
                    )

    assert len(medication_list) == 26
