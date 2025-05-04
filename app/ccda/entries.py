import uuid

from fhirclient.models import allergyintolerance, coding, condition
from fhirclient.models import medication as fhirmed
from fhirclient.models import medicationstatement

from .helpers import (
    code_with_translations,
    date_helper,
    effective_time_helper,
    generate_code,
    templateId,
)
from .models.base import (
    EntryRelationship,
    InstructionObservation,
    SubstanceAdministration,
)
from .models.datatypes import EIVL_TS, IVL_PQ, PIVL_TS


def medication(entry: medicationstatement.MedicationStatement, index: dict) -> dict:
    # http://www.hl7.org/ccdasearch/templates/2.16.840.1.113883.10.20.22.4.16.html

    # med = {
    #     "substanceAdministration": {
    #         "@classCode": "SBADM",
    #         "@moodCode": "INT",
    #     }
    # }

    # med["substanceAdministration"]["templateId"] = templateId(
    #     "2.16.840.1.113883.10.20.22.4.16", "2014-06-09"
    # )
    # med["substanceAdministration"]["id"] = {"@root": entry.identifier[0].value}
    # med["substanceAdministration"]["code"] = {
    #     "@code": "CONC",
    #     "@codeSystem": "2.16.840.1.113883.5.6",
    # }

    # med["substanceAdministration"]["statusCode"] = {"@code": entry.status}

    # # TODO add robust checking on this in case there's no high value
    # med["substanceAdministration"]["effectiveTime"] = {
    #     "low": {"@value": date_helper(entry.effectivePeriod.start.isostring)},
    #     "high": {"@value": date_helper(entry.effectivePeriod.end.isostring)},
    # }

    # referenced_med: fhirmed.Medication() = index[entry.medicationReference.reference]
    # request = index[entry.basedOn[0].reference]

    # # medication information
    # # http://www.hl7.org/ccdasearch/templates/2.16.840.1.113883.10.20.22.4.23.html
    # med["substanceAdministration"]["consumable"] = {}
    # med["substanceAdministration"]["consumable"]["manufacturedProduct"] = {
    #     "@classCode": "MANU",
    #     "templateId": templateId("2.16.840.1.113883.10.20.22.4.23", "2014-06-09"),
    #     "id": {"@root": uuid.uuid4()},
    #     "manufacturedMaterial": {
    #         # "code": [generate_code(x) for x in referenced_med.code.coding],
    #         "code": code_with_translations(referenced_med.code.coding).model_dump(
    #             by_alias=True, exclude_none=True
    #         ),
    #     },
    # }

    # med["substanceAdministration"]["entryRelationship"] = {
    #     "@typeCode": "SUBJ",
    #     "act": {
    #         "@classCode": "ACT",
    #         "@moodCode": "INT",
    #         "templateId": templateId("2.16.840.1.113883.10.20.22.4.20", "2014-06-09"),
    #         "code": {
    #             "@code": "422037009",
    #             "@displayName": "Provider medication administration instructions",
    #             "@codeSystemName": "SNOMED CT",
    #             "@codeSystem": "2.16.840.1.113883.6.96",
    #         },
    #         "text": {
    #             "#text": f"{entry.dosage[0].text}\n Patient Instuctions: {entry.dosage[0].patientInstruction}"
    #         },
    #         # "patientInstruction": {"#text": entry.dosage[0].patientInstruction},
    #         "statusCode": {"@code": "completed"},
    #     },
    # }
    # create a sample substance administration
    referenced_med: fhirmed.Medication = index[entry.medicationReference.reference]
    # request = index[entry.basedOn[0].reference]
    # dosage_instructions = request.dosageInstruction
    # for dose in dosage_instructions:
    #     print(dose.as_json())
    # print(dosage_instructions.as_json())
    substance_administration = SubstanceAdministration(
        templateId=templateId("2.16.840.1.113883.10.20.22.4.16", "2014-06-09"),
        id=[
            {
                "@root": entry.identifier[0].value,
                "@assigningAuthorityName": entry.identifier[0].system,
            }
        ],
        statusCode={"@code": entry.status},
        effectiveTime=effective_time_helper(entry.effectivePeriod),
        consumable={
            "manufacturedProduct": {
                "templateId": templateId(
                    root="2.16.840.1.113883.10.20.22.4.23", extension="2014-06-09"
                ),
                "id": {"@root": referenced_med.id},
                "manufacturedMaterial": {
                    "code": code_with_translations(referenced_med.code.coding),
                },
            }
        },
        entryRelationship=[],
    )
    # if dose quantiy is in dosage
    if entry.dosage[0].doseQuantity:
        # assumption that all structuered dosage will be snomed
        substance_administration.doseQuantity = {
            "value": {
                "@xsi:type": "PQ",
                "@nullFlavor": "OTH",
                "translation": {
                    "@value": entry.dosage[0].doseQuantity.value,
                    "@code": entry.dosage[0].doseQuantity.code,
                    "@codeSystem": entry.dosage[0].doseQuantity.system,
                    "originalText": entry.dosage[0].doseQuantity.unit,
                },
            },
        }
    # mapping from https://build.fhir.org/ig/HL7/ccda-on-fhir/CF-medications.html
    if entry.dosage[0].timing:
        # check if medication is prn
        if entry.dosage[0].timing.repeat.frequencyMax:
            # medicine is prn
            dose_period = (
                entry.dosage[0].timing.repeat.period
                / entry.dosage[0].timing.repeat.frequencyMax
            )

        else:
            # frequency is the occurrence per period. C-CDA has a single period between doses hence division
            dose_period = (
                entry.dosage[0].timing.repeat.period
                / entry.dosage[0].timing.repeat.frequency
            )
        # print(f"dose period: {dose_period}")
        substance_administration.effectiveTime.append(
            PIVL_TS(
                **{
                    "@xsi:type": "PIVL_TS",
                    "@operator": "A",
                    "@institutionSpecified": (
                        "true" if entry.dosage[0].timing.repeat.frequency else None
                    ),
                    "period": {
                        "@value": dose_period,
                        "@unit": entry.dosage[0].timing.repeat.periodUnit,
                    },
                }
            )
        )
        # https://hl7.org/fhir/R4/valueset-event-timing.html
        event_codes = [
            "MORN",
            "MORN.early",
            "MORN.late",
            "NOON",
            "AFT",
            "AFT.early",
            "AFT.late",
            "EVE",
            "EVE.early",
            "EVE.late",
            "NIGHT",
            "PHS",
            "HS",
            "WAKE",
            "C",
            "CM",
            "CD",
            "CV",
            "AC",
            "ACM",
            "ACD",
            "ACV",
            "PC",
            "PCM",
            "PCD",
            "PCV",
        ]
        # check if timing contains event codes
        if entry.dosage[0].timing.repeat.when:
            for event in entry.dosage[0].timing.repeat.when:
                if event in event_codes:
                    substance_administration.effectiveTime.append(
                        EIVL_TS(
                            **{
                                "@xsi:type": "EIVL_TS",
                                "@operator": "A",
                                "event": {
                                    "@code": event,
                                },
                            }
                        )
                    )

    #   check if route is in dosage
    if entry.dosage[0].method:
        substance_administration.routeCode = code_with_translations(
            entry.dosage[0].method.coding
        )

    for dose in entry.dosage:
        substance_administration.entryRelationship.append(
            EntryRelationship(
                **{
                    "sequenceNumber": (
                        entry.dosage.index(dose) + 1 if len(entry.dosage) > 1 else None
                    ),
                    "inversionInd": True,
                    "observation": InstructionObservation(
                        text=dose.text,
                        # free text must be in xsi type st for care everywhere to parse
                        value={
                            "@xsi:type": "ST",
                            "#text": dose.text,
                        },
                    ),
                }
            )
        )
    return {
        "substanceAdministration": substance_administration.model_dump(
            by_alias=True, exclude_none=True
        )
    }


def problem(entry: condition.Condition) -> dict:
    # http://www.hl7.org/ccdasearch/templates/2.16.840.1.113883.10.20.22.4.3.html
    prob = {
        "act": {
            "@classCode": "ACT",
            "@moodCode": "EVN",
        }
    }

    prob["act"]["templateId"] = templateId(
        "2.16.840.1.113883.10.20.22.4.3", "2015-08-01"
    )
    prob["act"]["id"] = {"@root": uuid.uuid4()}
    prob["act"]["code"] = {"@code": "CONC", "@codesystem": "2.16.840.1.113883.5.6"}

    prob["act"]["statusCode"] = {"@code": entry.clinicalStatus}
    prob["act"]["effectiveTime"] = {
        "low": {"@value": date_helper(entry.assertedDate.isostring)}
    }
    prob["act"]["entryRelationship"] = {"@typeCode": "SUBJ"}

    # http://www.hl7.org/ccdasearch/templates/2.16.840.1.113883.10.20.22.4.4.html
    observation = {"@classCode": "OBS", "@moodCode": "EVN"}
    observation["templateId"] = templateId(
        "2.16.840.1.113883.10.20.22.4.4", "2015-08-01"
    )
    observation["id"] = {"@root": uuid.uuid4()}
    observation["code"] = [
        {
            "@code": "64572001",
            "@displayName": "Condition",
            "@codeSystemName": "SNOMED CT",
            "@codeSystem": "2.16.840.1.113883.6.96",
        },
        {
            "@code": "75323-6",
            "@displayName": "Condition",
            "@codeSystemName": "LOINC",
            "@codeSystem": "2.16.840.1.113883.6.1",
        },
    ]
    observation["statusCode"] = {"@code": "completed"}
    observation["effectiveTime"] = {
        "low": {"@value": date_helper(entry.assertedDate.isostring)}
    }
    observation["value"] = {
        "@xsi:type": "CD",
        "@code": entry.code.coding[0].code,
        "@displayName": entry.code.coding[0].display,
        "@codeSystemName": "SNOMED CT",
        "@codeSystem": "2.16.840.1.113883.6.96",
    }

    prob["act"]["entryRelationship"]["observation"] = observation

    return prob


def allergy(entry: allergyintolerance.AllergyIntolerance) -> dict:
    # http://www.hl7.org/ccdasearch/templates/2.16.840.1.113883.10.20.22.4.30.html
    all = {
        "act": {
            "@classCode": "ACT",
            "@moodCode": "EVN",
        }
    }
    all["act"]["templateId"] = templateId(
        "2.16.840.1.113883.10.20.22.4.30", "2015-08-01"
    )
    all["act"]["id"] = {"@root": uuid.uuid4()}
    all["act"]["code"] = {"@code": "CONC", "@codeSystem": "2.16.840.1.113883.5.6"}

    # may need to be made dynamic if force to query old allergies
    all["act"]["statusCode"] = {"@code": "active"}
    all["act"]["effectiveTime"] = {
        "low": {"@value": date_helper(entry.assertedDate.isostring)}
    }
    all["act"]["entryRelationship"] = {"@typeCode": "SUBJ"}

    # http://www.hl7.org/ccdasearch/templates/2.16.840.1.113883.10.20.22.4.7.html
    observation = {"@classCode": "OBS", "@moodCode": "EVN"}
    observation["templateId"] = templateId(
        "2.16.840.1.113883.10.20.22.4.7", "2014-06-09"
    )
    observation["id"] = {"@root": uuid.uuid4()}
    observation["code"] = {"@code": "ASSERTION", "@codeSystem": "2.16.840.1.113883.5.4"}
    observation["statusCode"] = {"@code": "completed"}
    observation["value"] = {
        "@xsi:type": "CD",
        "@code": "416098002",
        "@displayName": "drug allergy",
        "@codeSystemName": "SNOMED CT",
        "@codeSystem": "2.16.840.1.113883.6.96",
    }

    observation["participant"] = {
        "@typeCode": "CSM",
        "participantRole": {
            "@classCode": "MANU",
            "playingEntity": {
                "@classCode": "MMAT",
                "code": {
                    "@code": entry.code.coding[0].code,
                    "@displayName": entry.code.coding[0].display,
                    "@codeSystemName": "SNOMED CT",
                    "@codeSystem": "2.16.840.1.113883.6.96",
                },
            },
        },
    }

    observation["entryRelationship"] = {
        "@typeCode": "MFST",
        "@inversionInd": "true",
        "observation": {
            "@classCode": "OBS",
            "@moodCode": "EVN",
            "templateId": templateId("2.16.840.1.113883.10.20.22.4.9", "2014-06-09"),
            "id": {"@root": uuid.uuid4()},
            "code": {"@code": "ASSERTION", "@codeSystem": "2.16.840.1.113883.5.4"},
            "effectiveTime": {
                "low": {"@value": date_helper(entry.assertedDate.isostring)}
            },
            "value": {
                "@xsi:type": "CD",
                "@code": entry.reaction[0].manifestation[0].coding[0].code,
                "@displayName": entry.reaction[0].manifestation[0].coding[0].display,
                "@codeSystemName": "SNOMED CT",
                "@codeSystem": "2.16.840.1.113883.6.96",
            },
        },
    }

    all["act"]["entryRelationship"]["observation"] = observation

    return all
