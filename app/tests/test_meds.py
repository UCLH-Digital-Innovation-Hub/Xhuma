import base64
import json
import xml.etree.ElementTree as ET
from email import message_from_string
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fhirclient.models import (
    bundle,
    immunization,
    medication,
    medicationrequest,
    medicationstatement,
)
from fhirclient.models import list as fhirlist

from app.ccda.convert_mime import base64_xml, convert_mime
from app.ccda.entries import _cda_period_from_repeat, _event_timing_warning
from app.ccda.entries import immunization_entry as immunization_entry_func
from app.ccda.entries import medication as medication_entry
from app.ccda.models.datatypes import CD
from app.ccda.models.dmd import DMDConcept, VPIProperty

med = medication.Medication(
    {
        "resourceType": "Medication",
        "id": "21",
        "meta": {"profile": ["https://fhir.nhs.uk/STU3/StructureDefinition/CareConnect-GPC-Medication-1"]},
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
        "meta": {"profile": ["https://fhir.nhs.uk/STU3/StructureDefinition/CareConnect-GPC-MedicationStatement-1"]},
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
        "dosage": [{"text": "2 tablets a day", "patientInstruction": "With evening meal"}],
    }
)
structured_med = medication.Medication(
    {
        "resourceType": "Medication",
        "id": "A37EA2D2-69D6-43C9-BB6F-66CF8D9D50F7",
        "meta": {"profile": ["https://fhir.nhs.uk/STU3/StructureDefinition/CareConnect-GPC-Medication-1"]},
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
med_request = medicationrequest.MedicationRequest(
    {
        "resourceType": "MedicationRequest",
        "id": "2E352BA6-8F87-479B-BC80-41494027F2E6",
        "meta": {"profile": ["https://fhir.nhs.uk/STU3/StructureDefinition/CareConnect-GPC-MedicationRequest-1"]},
        "extension": [
            {
                "url": "https://fhir.nhs.uk/STU3/StructureDefinition/Extension-CareConnect-GPC-MedicationRepeatInformation-1",
                "extension": [
                    {
                        "url": "numberOfRepeatPrescriptionsAllowed",
                        "valueUnsignedInt": 6,
                    },
                    {"url": "numberOfRepeatPrescriptionsIssued", "valueUnsignedInt": 0},
                ],
            },
            {
                "url": "https://fhir.nhs.uk/STU3/StructureDefinition/Extension-CareConnect-GPC-PrescriptionType-1",
                "valueCodeableConcept": {
                    "coding": [
                        {
                            "system": "https://fhir.nhs.uk/STU3/CodeSystem/CareConnect-PrescriptionType-1",
                            "code": "repeat",
                            "display": "Repeat",
                        }
                    ]
                },
            },
        ],
        "identifier": [
            {
                "system": "https://EMISWeb/A82038",
                "value": "7DC1C5D8540B4A7C8E19CBD3426A8CC62E352BA68F87479BBC8041494027F2E6",
            }
        ],
        "groupIdentifier": {"value": "2e352ba6-8f87-479b-bc80-41494027f2e6"},
        "status": "active",
        "intent": "plan",
        "medicationReference": {"reference": "Medication/A37EA2D2-69D6-43C9-BB6F-66CF8D9D50F7"},
        "subject": {"reference": "Patient/37"},
        "authoredOn": "2020-03-04T16:35:02.273+00:00",
        "recorder": {"reference": "Practitioner/2DB481A3-306A-4133-9491-1558161D6A2B"},
        "note": [{"text": "Patient Notes:Take 30 mins before a meal or snack"}],
        "dosageInstruction": [
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
        "dispenseRequest": {
            "validityPeriod": {"start": "2020-03-04"},
            "quantity": {"value": 28, "unit": "tablet"},
            "expectedSupplyDuration": {
                "value": 28,
                "unit": "day",
                "system": "http://unitsofmeasure.org",
                "code": "d",
            },
        },
    }
)
structured_statement = medicationstatement.MedicationStatement(
    {
        "resourceType": "MedicationStatement",
        "id": "2E352BA6-8F87-479B-BC80-41494027F2E6-MS",
        "meta": {"profile": ["https://fhir.nhs.uk/STU3/StructureDefinition/CareConnect-GPC-MedicationStatement-1"]},
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
        "basedOn": [{"reference": "MedicationRequest/2E352BA6-8F87-479B-BC80-41494027F2E6"}],
        "status": "active",
        "medicationReference": {"reference": "Medication/A37EA2D2-69D6-43C9-BB6F-66CF8D9D50F7"},
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


@pytest.mark.parametrize(
    ("repeat", "expected_low", "expected_high"),
    [
        (
            SimpleNamespace(frequency=1, frequencyMax=None, period=2, periodMax=3, periodUnit="h"),
            2,
            3,
        ),
        (
            SimpleNamespace(frequency=3, frequencyMax=4, period=1, periodMax=None, periodUnit="d"),
            0.25,
            1 / 3,
        ),
        (
            SimpleNamespace(
                frequency=None,
                frequencyMax=4,
                period=1,
                periodMax=None,
                periodUnit="d",
            ),
            0.25,
            1,
        ),
        (
            SimpleNamespace(frequency=2, frequencyMax=4, period=1, periodMax=2, periodUnit="d"),
            0.25,
            1,
        ),
    ],
)
def test_cda_period_from_variable_fhir_timing(repeat, expected_low, expected_high):
    period = _cda_period_from_repeat(repeat).model_dump(by_alias=True, exclude_none=True)

    assert period["@xsi:type"] == "IVL_PQ"
    assert period["low"] == {
        "@xsi:type": "IVXB_PQ",
        "@unit": repeat.periodUnit,
        "@value": pytest.approx(expected_low),
    }
    assert period["high"] == {
        "@xsi:type": "IVXB_PQ",
        "@unit": repeat.periodUnit,
        "@value": pytest.approx(expected_high),
    }


@pytest.mark.parametrize(
    ("repeat", "expected"),
    [
        (
            SimpleNamespace(when=["MORN", "AC"], offset=30),
            "Xhuma warning: Event-based medication timing: Morning (MORN); Before a meal (AC). Offset: 30 minutes.",
        ),
        (
            SimpleNamespace(when=["WAKE"], offset=None),
            "Xhuma warning: Event-based medication timing: Upon waking (WAKE).",
        ),
        (
            SimpleNamespace(when=["CUSTOM"], offset=None),
            "Xhuma warning: Event-based medication timing: Unknown event (CUSTOM).",
        ),
    ],
)
def test_event_timing_warning_uses_fhir_event_timing_valueset(repeat, expected):
    assert _event_timing_warning(repeat) == expected


# write tests to check if the pydantic models are working correctly
@pytest.mark.asyncio
async def test_substance_administration():
    """
    Test the SubstanceAdministration model
    """

    index_dict = {
        "Medication/21": med,
        "MedicationStatement/9": med,
        "MedicationRequest/32": med_request,
    }
    substance_administration = await medication_entry(med_statement, index_dict)
    assert substance_administration.row[0] == "22/05/2024"
    substance_administration = substance_administration.entry
    substance_administration = substance_administration["substanceAdministration"]

    assert substance_administration["@classCode"] == "SBADM"
    assert substance_administration["@moodCode"] == "INT"
    assert len(substance_administration["id"]) == 1
    # assert effective time list contains low
    assert substance_administration["effectiveTime"][0]["low"]["@value"] == "20240522000000+0100"


@pytest.mark.asyncio
@patch("app.ccda.entries.medication.dmd_lookup", new_callable=AsyncMock)
async def test_structured_dosage(mock_dmd_lookup):
    """
    Test the structured dosage
    """
    mock_dmd_lookup.return_value = DMDConcept(
        concept_id=1,
        valueString="Mock dm+d concept",
    )

    with open("app/tests/fixtures/bundles/9690937472.json", "r") as f:
        structured_dosage_bundle = json.load(f)

    fhir_bundle = bundle.Bundle(structured_dosage_bundle)

    # index resources to allow for resolution
    bundle_index = {}
    for entry in fhir_bundle.entry:
        try:
            address = f"{entry.resource.resource_type}/{entry.resource.id}"
            bundle_index[address] = entry.resource
        except Exception:
            pass
    medication_list = []
    for list in fhir_bundle.entry:
        if isinstance(list.resource, fhirlist.List):
            if list.resource.title == "Medications and medical devices":
                for entry in list.resource.entry:
                    referenced_item = bundle_index[entry.item.reference]

                    entry_with_row = await medication_entry(
                        referenced_item,
                        bundle_index,
                    )
                    entry_data = entry_with_row.entry
                    medication_list.append(entry_data)

                    assert entry_data["substanceAdministration"]["@classCode"] == "SBADM"
    assert len(medication_list) == 27


@pytest.mark.asyncio
@patch("app.ccda.entries.medication.dmd_lookup", new_callable=AsyncMock)
async def test_structured_detail(mock_dmd_lookup):
    mock_dmd_lookup.return_value = DMDConcept(
        concept_id=1,
        valueString="Mock dm+d concept",
    )

    index_dict = {
        "Medication/A37EA2D2-69D6-43C9-BB6F-66CF8D9D50F7": structured_med,
        "MedicationStatement/9": structured_med,
        "MedicationRequest/2E352BA6-8F87-479B-BC80-41494027F2E6": med_request,
    }

    substance_administration = await medication_entry(structured_statement, index_dict)
    substance_administration = substance_administration.entry
    substance_administration = substance_administration["substanceAdministration"]

    assert substance_administration["@classCode"] == "SBADM"
    assert substance_administration["@moodCode"] == "INT"
    assert (
        substance_administration["id"][0]["@extension"]
        == "7DC1C5D8540B4A7C8E19CBD3426A8CC62E352BA68F87479BBC8041494027F2E6MS"
    )
    assert substance_administration["effectiveTime"][0]["low"]["@value"] == "20200304"
    assert substance_administration["effectiveTime"][1]["@xsi:type"] == "PIVL_TS"
    assert substance_administration["effectiveTime"][1]["@institutionSpecified"] == "true"
    assert substance_administration["effectiveTime"][1]["period"]["@value"] == 1.0
    assert substance_administration["effectiveTime"][1]["period"]["@unit"] == "d"
    comment_text = next(
        relationship["act"]["text"]["xmlText"]
        for relationship in substance_administration["entryRelationship"]
        if relationship.get("act", {}).get("code", {}).get("@code") == "48767-8"
    )
    assert (
        "Xhuma warning: Event-based medication timing: Morning (MORN); Before a meal (AC). Offset: 30 minutes."
    ) in comment_text
    assert substance_administration["doseQuantity"]["@xsi:type"] == "PQ"
    assert substance_administration["doseQuantity"]["translation"]["@value"] == 1
    assert substance_administration["doseQuantity"]["translation"]["originalText"] == "tablet"
    assert substance_administration["doseQuantity"]["translation"]["@codeSystem"] == "2.16.840.1.113883.6.96"


new_structured_statement = medicationstatement.MedicationStatement(
    {
        "resourceType": "MedicationStatement",
        "id": "A07283C9-A77A-4850-8092-9AB8486D2865-MS",
        "meta": {"profile": ["https://fhir.nhs.uk/STU3/StructureDefinition/CareConnect-GPC-MedicationStatement-1"]},
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
        "basedOn": [{"reference": "MedicationRequest/A07283C9-A77A-4850-8092-9AB8486D2865"}],
        "status": "completed",
        "medicationReference": {"reference": "Medication/C60BB8CF-14D7-46F7-83A7-34007026F45E"},
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
        "meta": {"profile": ["https://fhir.nhs.uk/STU3/StructureDefinition/CareConnect-GPC-Medication-1"]},
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


@pytest.mark.asyncio
@patch("app.ccda.entries.medication.dmd_lookup", new_callable=AsyncMock)
async def test_new_structured_detail(mock_dmd_lookup):
    mock_dmd_lookup.return_value = DMDConcept(
        concept_id=212169831,
        valueString="Atenolol 100mg tablets",
        vpi=VPIProperty(unit="mg", value=100),
        route=CD(code="26643006", codeSystem="2.16.840.1.113883.6.96", displayName="Oral"),
    )
    index_dict = {
        "Medication/C60BB8CF-14D7-46F7-83A7-34007026F45E": new_med,
        "MedicationStatement/A07283C9-A77A-4850-8092-9AB8486D2865-MS": new_structured_statement,
        "MedicationRequest/A07283C9-A77A-4850-8092-9AB8486D2865": med_request,
    }
    substance_administration = await medication_entry(new_structured_statement, index_dict)
    warning = (
        'Xhuma: Route inferred as Oral from dm+d because the GP source value was "Take"; '
        "this may not represent the patient's actual route of administration. Verify during medicines reconciliation."
    )
    notes = _serialized_medication_notes(substance_administration)
    assert any(warning == " ".join("".join(note.itertext()).split()) for note in notes)
    substance_administration = substance_administration.entry
    substance_administration = substance_administration["substanceAdministration"]
    assert substance_administration["routeCode"]["@displayName"] == "Oral"
    assert any(
        warning in relationship.get("act", {}).get("text", {}).get("xmlText", "")
        for relationship in substance_administration["entryRelationship"]
    )

    assert substance_administration["@classCode"] == "SBADM"
    assert substance_administration["@moodCode"] == "INT"
    assert substance_administration["doseQuantity"]["@xsi:type"] == "PQ"
    assert substance_administration["doseQuantity"]["@value"] == 100
    assert substance_administration["doseQuantity"]["@unit"] == "mg"


@pytest.mark.asyncio
async def test_immunization_entry_with_notes():
    # Test case 1.9 style immunization with note & explanation reason
    imm_data = {
        "resourceType": "Immunization",
        "id": "imm-1",
        "status": "completed",
        "primarySource": True,
        "notGiven": False,
        "patient": {"reference": "Patient/2"},
        "date": "2026-03-12T10:00:00Z",
        "vaccineCode": {
            "coding": [
                {
                    "system": "http://snomed.info/sct",
                    "code": "1119349007",
                    "display": "Comirnaty Omicron XBB.1.5 injection",
                }
            ]
        },
        "lotNumber": "LOT123",
        "note": [{"text": "Notes on adminstration of vaccine"}],
        "explanation": {
            "reason": [
                {
                    "coding": [
                        {
                            "system": "http://snomed.info/sct",
                            "code": "171279008",
                            "display": "Immunisation due",
                        }
                    ]
                }
            ]
        },
    }
    imm_resource = immunization.Immunization(imm_data)
    entry_with_row = immunization_entry_func(imm_resource, {})
    entry = entry_with_row.entry
    row = entry_with_row.row

    assert entry["templateId"][0]["@root"] == "2.16.840.1.113883.10.20.22.4.52"
    assert entry["statusCode"]["@code"] == "completed"
    assert len(entry["entryRelationship"]) == 1
    assert entry["entryRelationship"][0]["act"]["code"]["@code"] == "48767-8"
    assert "Notes on adminstration of vaccine" in entry["entryRelationship"][0]["act"]["text"]["xmlText"]
    assert "Reason: Immunisation due" in entry["entryRelationship"][0]["act"]["text"]["xmlText"]
    # Check narrative row formatting
    assert row[0] == "12/03/2026"
    assert row[1]["content"][0] == {"@styleCode": "Bold", "#text": "Comirnaty Omicron XBB.1.5 injection"}
    assert "Notes on adminstration of vaccine" in row[1]["content"][1]["#text"]
    assert "Reason: Immunisation due" in row[1]["content"][1]["#text"]
    assert row[2] == "LOT123"
    assert row[3] == "completed"


@pytest.mark.asyncio
async def test_medication_repeats_robustness():
    # Test case 2.4 style repeats using valuePositiveInt instead of valueUnsignedInt for repeats issued
    med_request_data = {
        "resourceType": "MedicationRequest",
        "id": "med-req-1",
        "identifier": [
            {
                "system": "https://EMISWeb/A82038",
                "value": "7DC1C5D8540B4A7C8E19CBD3426A8CC62E352BA68F87479BBC8041494027F2E6",
            }
        ],
        "extension": [
            {
                "url": "https://fhir.nhs.uk/STU3/StructureDefinition/Extension-CareConnect-GPC-MedicationRepeatInformation-1",
                "extension": [
                    {
                        "url": "numberOfRepeatPrescriptionsAllowed",
                        "valuePositiveInt": 3,
                    },
                    {
                        "url": "numberOfRepeatPrescriptionsIssued",
                        "valuePositiveInt": 1,
                    },
                ],
            }
        ],
        "status": "active",
        "intent": "plan",
        "medicationReference": {"reference": "Medication/21"},
        "subject": {"reference": "Patient/2"},
    }

    med_statement_data = {
        "resourceType": "MedicationStatement",
        "id": "med-stat-1",
        "identifier": [
            {
                "system": "https://fhir.nhs.uk/Id/cross-care-setting-identifier",
                "value": "398c49aa-1933-11f0-b9fc-00505692d4aa",
            }
        ],
        "basedOn": [{"reference": "MedicationRequest/med-req-1"}],
        "status": "active",
        "medicationReference": {"reference": "Medication/21"},
        "effectivePeriod": {"start": "2026-01-21T00:00:00+00:00"},
        "subject": {"reference": "Patient/2"},
        "taken": "unk",
        "dosage": [{"text": "take as directed"}],
    }

    index_dict = {
        "Medication/21": med,
        "MedicationRequest/med-req-1": medicationrequest.MedicationRequest(med_request_data),
    }

    statement = medicationstatement.MedicationStatement(med_statement_data)
    entry_with_row = await medication_entry(statement, index_dict)

    # Check that Prescription repeat information was parsed and added to prescription_information (row[7])
    row = entry_with_row.row
    assert "Prescription 1 of 3 allowed repeats." in _narrative_cell_text(row[7])


@pytest.mark.asyncio
@patch("app.ccda.entries.medication.dmd_lookup", new_callable=AsyncMock)
async def test_medication_notes_ordering_and_inclusion(mock_dmd_lookup):
    # Setup mock data to test chronological order of notes and that issued quantity / repeats are included in XML
    med_request_data = {
        "resourceType": "MedicationRequest",
        "id": "med-req-notes-1",
        "identifier": [{"system": "system", "value": "val"}],
        "note": [
            {"text": "Note 1"},
            {"text": "Note 2"},
            {"text": "Note 3"},
        ],
        "extension": [
            {
                "url": "https://fhir.nhs.uk/STU3/StructureDefinition/Extension-CareConnect-GPC-MedicationStatusReason-1",
                "extension": [
                    {
                        "url": "statusReason",
                        "valueCodeableConcept": {"text": "Patient requested stop"},
                    }
                ],
            },
            {
                "url": "https://fhir.nhs.uk/STU3/StructureDefinition/Extension-CareConnect-GPC-MedicationRepeatInformation-1",
                "extension": [
                    {
                        "url": "numberOfRepeatPrescriptionsAllowed",
                        "valuePositiveInt": 6,
                    },
                    {"url": "numberOfRepeatPrescriptionsIssued", "valuePositiveInt": 1},
                ],
            },
        ],
        "dispenseRequest": {
            "quantity": {"value": 30, "unit": "tablets"},
            "validityPeriod": {"end": "2026-02-21T00:00:00+00:00"},
        },
        "status": "stopped",
        "intent": "plan",
        "medicationReference": {"reference": "Medication/21"},
        "subject": {"reference": "Patient/2"},
    }

    med_statement_data = {
        "resourceType": "MedicationStatement",
        "id": "med-stat-notes-1",
        "identifier": [{"system": "system", "value": "val"}],
        "basedOn": [{"reference": "MedicationRequest/med-req-notes-1"}],
        "status": "active",
        "medicationReference": {"reference": "Medication/21"},
        "effectivePeriod": {"start": "2026-01-21T00:00:00+00:00"},
        "subject": {"reference": "Patient/2"},
        "taken": "unk",
        "note": [{"text": "Note 4"}, {"text": "Note 5"}],
        "dosage": [{"text": "take as directed"}],
    }

    index_dict = {
        "Medication/21": med,
        "MedicationRequest/med-req-notes-1": medicationrequest.MedicationRequest(med_request_data),
    }

    statement = medicationstatement.MedicationStatement(med_statement_data)
    entry_with_row = await medication_entry(statement, index_dict)

    # Check that chronological order is preserved and duplicates are handled if any
    # Retrieve the structured XML notes from entry
    xml_dict = entry_with_row.entry
    relationships = xml_dict.get("substanceAdministration", {}).get("entryRelationship", [])

    # Find the comment activity (code 48767-8)
    comment_act = None
    for rel in relationships:
        act = rel.get("act", {})
        if act.get("code", {}).get("@code") == "48767-8":
            comment_act = act
            break

    assert comment_act is not None, "Comment activity not found in entryRelationship"

    xml_text = comment_act.get("text", {}).get("xmlText", "")
    br_notes = [n.strip() for n in xml_text.split("\n") if n.strip()]

    # Check that they exist and their relative order is correct
    idx_note1 = next(i for i, n in enumerate(br_notes) if "Note 1" in n)
    idx_note5 = next(i for i, n in enumerate(br_notes) if "Note 5" in n)
    idx_status = next(i for i, n in enumerate(br_notes) if "Medication status reason: Patient requested stop" in n)

    assert idx_note1 < idx_note5, "Base notes are out of chronological order!"
    assert idx_note5 < idx_status, "Extensions are out of order!"

    # Check that repeats and issued quantity were correctly moved to prescription_information (row[7])
    row = entry_with_row.row
    assert "Prescription 1 of 6 allowed repeats." in _narrative_cell_text(row[7]), (
        "Repeats missing from prescription info"
    )
    assert "Issued quantity: 30 tablets" in _narrative_cell_text(row[7]), (
        "Issued quantity missing from prescription info"
    )


def _narrative_cell_text(cell):
    root = ET.fromstring(base64.b64decode(base64_xml({"td": cell})))
    return " ".join("".join(root.itertext()).split())


def _serialized_medication_notes(result):
    document = {
        "section": {
            "@xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
            "text": {"table": {"tbody": {"tr": {"td": result.row}}}},
            "entry": result.entry,
        }
    }
    root = ET.fromstring(base64.b64decode(base64_xml(document)))
    structured_notes = next(
        rel["act"]["text"]["xmlText"]
        for rel in result.entry["substanceAdministration"]["entryRelationship"]
        if rel.get("act", {}).get("code", {}).get("@code") == "48767-8"
    )
    comment = root.find("entry/substanceAdministration/entryRelationship/act/code[@code='48767-8']/..")
    assert comment.findtext("text/xmlText") == structured_notes
    assert comment.find(".//content") is None
    assert comment.find(".//br") is None
    cells = root.find("text/table/tbody/tr").findall("td")
    # The medication name is the row's primary visual anchor, not the dates or status.
    name = cells[4].find("content")
    assert name.attrib == {"styleCode": "Bold"}
    material_code = root.find("entry/substanceAdministration/consumable/manufacturedProduct/manufacturedMaterial/code")
    assert name.text == material_code.get("displayName")
    assert len(name) == 0
    assert all(cell.find(".//*[@styleCode='Bold']") is None for cell in cells[:4])
    return cells[6]


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["dmd", "freetext", "multiple", "event", "combined", "collision"])
async def test_medication_narrative_bolds_only_authored_provenance_markers(scenario):
    dose_warning = "Xhuma: Dose of 100 mg automatically mapped via dm+d lookup"
    route_warning = (
        'Xhuma: Route inferred as Oral from dm+d because the GP source value was "Take"; '
        "this may not represent the patient's actual route of administration. Verify during medicines reconciliation."
    )
    free_warning = "Xhuma: Free-text dosage instructions automatically mapped"
    multiple_warning = "Xhuma: Multiple dosage instructions found. Use caution when converting dose**"
    source_note = dose_warning if scenario == "collision" else "Xhuma: Supplier note & clinical advice"
    statement_data = structured_statement.as_json()
    statement_data["note"] = [{"text": source_note}]
    dosage = statement_data["dosage"][0]
    dosage.pop("timing")
    if scenario in ("dmd", "combined"):
        dosage["method"] = {"coding": [{"system": "http://snomed.info/sct", "code": "419652001", "display": "Take"}]}
    if scenario in ("multiple", "combined"):
        dosage["timing"] = {"repeat": {"when": ["MORN"]}}
        statement_data["dosage"].append({"text": "Second dosage"})
    if scenario == "event":
        dosage["timing"] = {"repeat": {"when": ["MORN", "AC"], "offset": 30}}
    statement = medicationstatement.MedicationStatement(statement_data)
    request_data = med_request.as_json()
    request_data["note"] = [{"text": "Ordinary supplier note"}]
    request = medicationrequest.MedicationRequest(request_data)
    index = {
        statement.medicationReference.reference: structured_med,
        statement.basedOn[0].reference: request,
    }
    concept = DMDConcept(
        concept_id=1,
        valueString="Mock dm+d concept",
        vpi=VPIProperty(unit="mg", value=100),
        route=CD(code="26643006", codeSystem="2.16.840.1.113883.6.96", displayName="Oral"),
    )
    with (
        patch("app.ccda.entries.medication.dmd_lookup", new_callable=AsyncMock, return_value=concept),
        patch(
            "app.ccda.entries.medication.mapped_dosages",
            return_value=SimpleNamespace(
                dosages=statement.dosage, freetext_mapped=scenario in ("freetext", "combined")
            ),
        ),
    ):
        result = await medication_entry(statement, index)

    generated = {
        "dmd": [dose_warning, route_warning],
        "freetext": [dose_warning, free_warning],
        "multiple": [multiple_warning, "Xhuma warning: Event-based medication timing for dosage 1: Morning (MORN)."],
        "event": [
            dose_warning,
            "Xhuma warning: Event-based medication timing: Morning (MORN); Before a meal (AC). Offset: 30 minutes.",
        ],
        "combined": [
            route_warning,
            free_warning,
            multiple_warning,
            "Xhuma warning: Event-based medication timing for dosage 1: Morning (MORN).",
        ],
        "collision": [],
    }[scenario]
    expected = ["Ordinary supplier note", source_note, *generated]
    cell = _serialized_medication_notes(result)
    assert cell.find(".//BR") is None
    assert len(cell.findall(".//br")) == len(expected)
    # xmltodict's pretty printing inserts whitespace between child content and its tail.
    assert [" ".join("".join(note.itertext()).split()) for note in cell] == expected
    for note, text in zip(cell, expected):
        assert note.tag == "content"
        assert note.attrib == {}
        assert note[-1].tag == "br"
        bold = note.find("content")
        should_bold = text in generated
        if should_bold:
            assert bold is not None
            assert bold.attrib == {"styleCode": "Bold"}
            marker = "Xhuma:" if text in (dose_warning, route_warning, free_warning, multiple_warning) else "Xhuma"
            assert bold.text == marker
            assert len(bold) == 0
            assert bold.tail.strip() == text[len(marker) :].strip()
            assert list(note) == [bold, note[-1]]
            assert note[-1].attrib == {}
        else:
            assert bold is None
    assert len(cell.findall(".//*[@styleCode]")) == len(generated)
    structured = next(
        rel["act"]["text"]["xmlText"]
        for rel in result.entry["substanceAdministration"]["entryRelationship"]
        if rel.get("act", {}).get("code", {}).get("@code") == "48767-8"
    )
    assert structured == "\n".join(expected)
    assert "<br" not in structured


@pytest.fixture(params=["base64", "mime"])
def medication_xml(request):
    def serialize(document):
        if request.param == "base64":
            return ET.fromstring(base64.b64decode(base64_xml(document)))
        message = message_from_string(convert_mime(document))
        parts = [part for part in message.walk() if part.get_content_type() == "application/xop+xml"]
        assert len(parts) == 1
        return ET.fromstring(parts[0].get_payload(decode=True))

    return serialize


@pytest.mark.asyncio
@pytest.mark.parametrize("multiple", [False, True])
async def test_medication_labels_serialized_without_changing_clinical_data(multiple, medication_xml):
    source_notes = [
        "Prescriber Notes:Keep: wording & <value>",
        "Patient Notes:issue number 2 Adding message when prescribing 2nd time",
        "Additional Information:  retain leading spaces: and colon",
        "Transfer degraded medication text:source transfer",
        "Clinical context: arbitrary colon",
        "Xhuma: source warning",
        "Xhuma warning: source event",
        "Instructions: source label is not allowlisted",
        "Medication status reason: source status is not allowlisted",
        "Last issued date: source date is not allowlisted",
        "Issued quantity: source quantity is not allowlisted",
        "Embedded Patient Notes:not a prefix",
    ]
    statement_data = structured_statement.as_json()
    statement_data["extension"] = med_statement.as_json()["extension"]
    statement_data["note"] = [{"text": source_notes[0]}, {"text": "Keep: wording & <value>"}]
    statement_data["dosage"][0]["patientInstruction"] = "First advice: with food"
    if multiple:
        statement_data["dosage"].append(
            {
                "text": "Second dose: at night",
                "patientInstruction": "Second advice: with water",
                "timing": {"repeat": {"when": ["HS"]}},
                "doseQuantity": {"value": 2, "unit": "tablet"},
            }
        )
    request_data = med_request.as_json()
    request_data["note"] = [{"text": note} for note in source_notes]
    request_data["extension"].append(
        {
            "url": "https://fhir.nhs.uk/STU3/StructureDefinition/Extension-CareConnect-GPC-MedicationStatusReason-1",
            "extension": [{"url": "statusReason", "valueCodeableConcept": {"text": "Stopped: patient choice"}}],
        }
    )
    med_data = structured_med.as_json()
    med_data["code"]["coding"].append({"system": "http://snomed.info/sct", "code": "196421000000109"})
    med_data["code"]["text"] = "Transferred: original wording"
    statement = medicationstatement.MedicationStatement(statement_data)
    request = medicationrequest.MedicationRequest(request_data)
    medicine = medication.Medication(med_data)
    index = {statement.medicationReference.reference: medicine, statement.basedOn[0].reference: request}
    before = [resource.as_json() for resource in (statement, request, medicine)]
    with (
        patch(
            "app.ccda.entries.medication.dmd_lookup",
            new_callable=AsyncMock,
            return_value=DMDConcept(concept_id=1, valueString="No mapping"),
        ),
        patch(
            "app.ccda.entries.medication.mapped_dosages",
            return_value=SimpleNamespace(dosages=statement.dosage, freetext_mapped=False),
        ),
    ):
        result = await medication_entry(statement, index)
        # Presentation changes must not enter any structured dosage, text, or relationship.
        with patch(
            "app.ccda.entries.medication._labelled_narrative",
            side_effect=lambda label, value: {"#text": f"{label} {value}<br />"},
        ):
            plain = await medication_entry(statement, index)
    assert result.entry == plain.entry
    assert [resource.as_json() for resource in (statement, request, medicine)] == before
    _serialized_medication_notes(result)
    root = medication_xml(
        {
            "section": {
                "@xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
                "text": {"table": {"tr": {"td": result.row}}},
                "entry": result.entry,
            }
        }
    )
    cells = root.findall("text/table/tr/td")
    assert root.find(".//BR") is None
    prefix = "1. " if multiple else ""
    instruction_values = [
        prefix + statement.dosage[0].text,
        prefix + statement.dosage[0].patientInstruction,
    ]
    for i, (line, label, value) in enumerate(
        zip(cells[5], ["Instructions:", "Patient Instructions:"], instruction_values)
    ):
        bold, normal = list(line)[:2]
        assert bold.attrib == {"styleCode": "Bold"}
        assert bold.text == label
        assert normal.attrib == {}
        assert normal.text == " " + value
        assert len(normal) == 0
        assert [child.tag for child in line] == ["content"] * (3 if multiple else 2) + (["br"] if i == 0 else [])
        if multiple:
            continuation = line[2]
            assert [child.tag for child in continuation] == ["br", "content"]
            assert continuation[1].text == ["2. Second dose: at night", "2. Second advice: with water"][i]
        assert "<br" not in "".join(line.itertext())
    assert len(cells[5].findall(".//br")) == 1 + 2 * int(multiple)
    expected_labels = {
        "Prescriber Notes:": "Keep: wording & <value>",
        "Patient Notes:": "issue number 2 Adding message when prescribing 2nd time",
        "Additional Information:": "  retain leading spaces: and colon",
        "Transfer degraded medication text:": "source transfer",
        "Medication status reason:": "Stopped: patient choice",
        "Last issued date:": "28/02/2025",
        "Issued quantity:": "28 tablet",
    }
    labelled = [line for cell in cells[6:] for line in cell if len(line.findall("content")) == 2]
    assert len(labelled) == 8  # Includes both source and generated transfer text.
    for line in labelled:
        bold, normal = list(line)[:2]
        assert bold.attrib == {"styleCode": "Bold"}
        assert len(bold) == 0
        assert normal.attrib == {}
        assert len(normal) == 0
        expected = expected_labels[bold.text]
        if normal.text == "  Transferred: original wording":
            assert bold.text == "Transfer degraded medication text:"
            expected = " Transferred: original wording"
        # Exactly one added separator, then the untouched source remainder.
        assert normal.text == " " + expected
        assert [child.tag for child in line] == ["content", "content"] + (
            [] if bold.text == "Issued quantity:" else ["br"]
        )
        assert "<br" not in "".join(line.itertext())
    for line, source in zip(list(cells[6])[4:], source_notes[4:]):
        assert line.find("content") is None
        # Legacy plain notes still carry a literal break in MIME, unchanged here.
        suffix = " <br />" if line.find("br") is None else ""
        assert "".join(line.itertext()).strip() == source + suffix
    assert all(line.find(".//*[@styleCode]") is None for line in cells[7] if "Prescription" in "".join(line.itertext()))
    generated = []
    if multiple:
        generated.append("Xhuma: Multiple dosage instructions found. Use caution when converting dose**")
    generated.append(_event_timing_warning(statement.dosage[0].timing.repeat, 1 if multiple else None))
    if multiple:
        generated.append(_event_timing_warning(statement.dosage[1].timing.repeat, 2))
    expected_notes = (
        source_notes
        + ["Transfer degraded medication text: Transferred: original wording"]
        + generated
        + ["Medication status reason: Stopped: patient choice"]
    )
    relationships = result.entry["substanceAdministration"]["entryRelationship"]
    assert relationships[0]["substanceAdministration"]["text"] == "; ".join(
        f"{i}. {dose.text}" if multiple else dose.text for i, dose in enumerate(statement.dosage, 1)
    )
    assert relationships[1]["act"]["text"]["xmlText"] == "; ".join(
        f"{i}. {dose.patientInstruction}" if multiple else dose.patientInstruction
        for i, dose in enumerate(statement.dosage, 1)
    )
    assert relationships[2]["act"]["text"]["xmlText"] == "\n".join(expected_notes)
    assert len(cells[6].findall(".//br")) in (6, len(expected_notes))
    assert len(cells[7].findall(".//br")) == 3
    for i, line in enumerate(cells[7]):
        assert (line[-1].tag == "br") == (i < 3)
        assert "<br" not in "".join(line.itertext())
    comment = root.find("entry/substanceAdministration/entryRelationship/act/code[@code='48767-8']/..")
    assert comment.findtext("text/xmlText") == "\n".join(expected_notes)
    assert comment.find(".//br") is None
    assert comment.find(".//content") is None


@pytest.mark.asyncio
async def test_single_prescription_item_has_no_trailing_break(medication_xml):
    statement_data = med_statement.as_json()
    statement_data.pop("extension")
    request_data = med_request.as_json()
    request_data.pop("extension")
    statement = medicationstatement.MedicationStatement(statement_data)
    request = medicationrequest.MedicationRequest(request_data)
    with patch(
        "app.ccda.entries.medication.mapped_dosages",
        return_value=SimpleNamespace(dosages=statement.dosage, freetext_mapped=False),
    ):
        result = await medication_entry(
            statement,
            {
                statement.medicationReference.reference: med,
                statement.basedOn[0].reference: request,
            },
        )
    cell = medication_xml({"td": result.row[7]})
    assert cell.find(".//br") is None
    assert "<br" not in "".join(cell.itertext())
    line = cell.find("content")
    assert [child.tag for child in line] == ["content", "content"]
    assert line[0].text == "Issued quantity:"
    assert line[0].attrib == {"styleCode": "Bold"}
    assert line[1].text == " 28 tablet"
    assert line[1].attrib == {}
