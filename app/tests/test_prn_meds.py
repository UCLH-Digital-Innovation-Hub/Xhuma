import json
import pprint
from unittest.mock import patch

from fhirclient.models import bundle
from fhirclient.models import list as fhirlist
from fhirclient.models import medication, medicationstatement

from app.ccda.entries import medication as medication_entry

prn_statement = medicationstatement.MedicationStatement(
    {
        "resourceType": "MedicationStatement",
        "id": "1000000000000000_71eff60000000000",
        "meta": {
            "profile": [
                "https://fhir.nhs.uk/STU3/StructureDefinition/CareConnect-GPC-MedicationStatement-1"
            ]
        },
        "extension": [
            {
                "url": "https://fhir.nhs.uk/STU3/StructureDefinition/Extension-CareConnect-GPC-MedicationStatementLastIssueDate-1",
                "valueDateTime": "2020-02-25",
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
        ],
        "identifier": [
            {
                "system": "https://tpp-uk.com/Id/ccs-id",
                "value": "1000000000000000_71eff60000000000",
            }
        ],
        "basedOn": [
            {"reference": "MedicationRequest/1000000000000000_71eff60000000000_plan"}
        ],
        "context": {"reference": "Encounter/4000000000000000_1e6a090000000000"},
        "status": "active",
        "medicationReference": {"reference": "Medication/1004837_1"},
        "effectivePeriod": {"start": "2020-02-25", "end": "2020-03-24"},
        "dateAsserted": "2020-02-25",
        "subject": {"reference": "Patient/37"},
        "taken": "unk",
        "dosage": [
            {
                "text": "1 drop, twice a day",
                "timing": {
                    "repeat": {"frequencyMax": 4, "period": 1, "periodUnit": "d"}
                },
                "doseQuantity": {
                    "value": 1,
                    "unit": "drop",
                    "system": "http://snomed.info/sct",
                    "code": "10693611000001100",
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


def test_prn_medication_statement():
    """Test the conversion of a PRN medication statement to a CCDA entry."""
    # Convert the FHIR MedicationStatement to a CCDA entry
    index_dict = {
        "Medication/1004837_1": prn_med,
        "prn_medicationStatement/9": prn_med,
    }
    substance_administration = medication_entry(prn_statement, index_dict)
    substance_administration = substance_administration["substanceAdministration"]
    # Print the CCDA entry for debugging purposes
    pprint.pprint(substance_administration)

    # Check that the entry is not None
    assert substance_administration is not None

    # Check that the entry has the expected structure
    assert "precondition" in substance_administration
    assert substance_administration["precondition"]["@typeCode"] == "PRCN"
    assert (
        substance_administration["precondition"]["criterion"]["templateId"][0]["@root"]
        == "2.16.840.1.113883.10.20.22.4.25"
    )
    assert (
        substance_administration["precondition"]["criterion"]["code"]["@code"]
        == "ASSERTION"
    )
    assert (
        substance_administration["precondition"]["criterion"]["value"]["@nullFlavor"]
        == "NI"
    )
    # Check the medication details
