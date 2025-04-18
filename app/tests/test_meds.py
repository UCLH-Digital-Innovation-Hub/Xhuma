from fhirclient.models import medication, medicationstatement

from app.ccda.helpers import date_helper, templateId
from app.ccda.models.base import SubstanceAdministration
from app.ccda.models.datatypes import II

med_statement = medication.Medication(
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
def test_substance_administration():
    """
    Test the SubstanceAdministration model
    """

    # create a sample substance administration
    substance_administration = SubstanceAdministration(
        id=II(
            root=med_statement.identifier[0].value,
            assigningAuthorityName="https://fhir.nhs.uk/Id/cross-care-setting-identifier",
        ),
        statusCode={"@code": med_statement.status},
        effectiveTime={
            "@value": date_helper(med_statement.effectivePeriod.start.isostring),
        },
        # entryRelationship=[
        #     {
        #         "@typeCode": "SUBJ",
        #         "@inversionInd": False,
        #         "@moodCode": med_statement.resource.status,
        #         "@classCode": med_statement.resource.status,
        #         "@templateId": templateId(
        #             "2.16.840.1.113883.10.20.22.4.16", "2014-06-09"
        #         ),
        #         "@code": {
        #             "@codeSystemName": med_statement.resource.status,
        #             "@codeSystemName": med_statement.resource.status,
        #             "@displayName": med_statement.resource.status,
        #             "@codeSystemName": med_statement.resource.status,
        #             "@displayName": med_statement.resource.status,
        #             "@codeSystemName": med_statement.resource.status,
        #             "@displayName": med_statement.resource.status,
        #         }
        #     }
        # ]
    )

    assert substance_administration.classCode == "SBADM"
    assert substance_administration.moodCode == "EVN"
    assert len(substance_administration.id) == 1
    assert substance_administration.id[0]["@root"] is not None
