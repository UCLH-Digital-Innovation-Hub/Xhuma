import uuid
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional, Sequence

from fhirclient.models import (allergyintolerance, coding, condition,
                               immunization)
from fhirclient.models import medication as fhirmed
from fhirclient.models import medicationstatement, observation

from .helpers import (code_with_translations, date_helper,
                      effective_time_helper, organization_to_author,
                      templateId)
from .models.base import (EntryRelationship, Observation, ResultObservation,
                          ResultsOrganizer, SubstanceAdministration)
from .models.datatypes import EIVL_TS, IVL_PQ, IVL_TS, PIVL_TS, PQ

Cell = str
Row = List[Cell]


@dataclass(frozen=True)
class EntryWithRow:
    entry: Any  # C-CDA entry section
    row: Optional[Row]  # row data for summary table in section


def medication(
    entry: medicationstatement.MedicationStatement, index: dict
) -> EntryWithRow:
    # http://www.hl7.org/ccdasearch/templates/2.16.840.1.113883.10.20.22.4.16.html

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
                # root for url base id
                # https://build.fhir.org/ig/HL7/ccda-on-fhir/mappingGuidance.html#fhir-identifier--cda-id-with-example-mapping
                "@root": entry.identifier[0].system,
                "@extension": entry.identifier[0].value,
            }
        ],
        statusCode={"@code": entry.status},
        effectiveTime=effective_time_helper(entry.effectivePeriod),
        consumable={
            "manufacturedProduct": {
                "templateId": templateId(
                    root="2.16.840.1.113883.10.20.22.4.23", extension="2014-06-09"
                ),
                "id": {
                    "@root": referenced_med.id,
                },
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
        # substance_administration.doseQuantity = {
        #     "value": {
        #         "@xsi:type": "PQ",
        #         "@nullFlavor": "OTH",
        #         "translation": {
        #             "@value": entry.dosage[0].doseQuantity.value,
        #             "@code": entry.dosage[0].doseQuantity.code,
        #             "@codeSystemName": entry.dosage[0].doseQuantity.system,
        #             "@codeSystem": "2.16.840.1.113883.6.96",
        #             "originalText": entry.dosage[0].doseQuantity.unit,
        #         },
        #     },
        # }
        substance_administration.doseQuantity = {
            "@xsi:type": "PQ",
            "@value": entry.dosage[0].doseQuantity.value,
        }
        if entry.dosage[0].doseQuantity.unit:
            substance_administration.doseQuantity["@unit"] = entry.dosage[
                0
            ].doseQuantity.unit

        # if there is a code add a translation
        if entry.dosage[0].doseQuantity.code:
            substance_administration.doseQuantity["translation"] = {
                "@value": entry.dosage[0].doseQuantity.value,
                "@code": entry.dosage[0].doseQuantity.code,
                "@codeSystem": "2.16.840.1.113883.6.96",
                "originalText": entry.dosage[0].doseQuantity.unit,
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

            # populate precondition
            substance_administration.precondition = {
                "@typeCode": "PRCN",
                "criterion": {
                    "templateId": templateId(
                        root="2.16.840.1.113883.10.20.22.4.25", extension="2014-06-09"
                    ),
                    "code": {
                        "@code": "ASSERTION",
                        "@codeSystem": "2.16.840.1.113883.5.4",
                    },
                },
            }
            # if there is a asNeededCodeableConcept, use it
            if entry.dosage[0].asNeededCodeableConcept:
                substance_administration.precondition["criterion"]["value"] = {
                    "@xsi:type": "CD",
                    "@code": entry.dosage[0].asNeededCodeableConcept.coding[0].code,
                    "@displayName": entry.dosage[0]
                    .asNeededCodeableConcept.coding[0]
                    .display,
                    "@codeSystemName": entry.dosage[0]
                    .asNeededCodeableConcept.coding[0]
                    .value,
                }
            else:
                # if no asNeededCodeableConcept, use NI
                substance_administration.precondition["criterion"]["value"] = {
                    "@xsi:type": "CD",
                    "@nullFlavor": "NI",
                }

        else:
            # frequency is the occurrence per period. C-CDA has a single period between doses hence division
            dose_period = (
                entry.dosage[0].timing.repeat.period
                / entry.dosage[0].timing.repeat.frequency
            )
        # print(f"dose period: {dose_period}")

        # https://hl7.org/fhir/R4/valueset-event-timing.html
        # event_codes = [
        #     "MORN",
        #     "MORN.early",
        #     "MORN.late",
        #     "NOON",
        #     "AFT",
        #     "AFT.early",
        #     "AFT.late",
        #     "EVE",
        #     "EVE.early",
        #     "EVE.late",
        #     "NIGHT",
        #     "PHS",
        #     "HS",
        #     "WAKE",
        #     "C",
        #     "CM",
        #     "CD",
        #     "CV",
        #     "AC",
        #     "ACM",
        #     "ACD",
        #     "ACV",
        #     "PC",
        #     "PCM",
        #     "PCD",
        #     "PCV",
        # ]
        # # check if timing contains event codes
        # if entry.dosage[0].timing.repeat.when:
        #     for event in entry.dosage[0].timing.repeat.when:
        #         if event in event_codes:
        #             substance_administration.effectiveTime.append(
        #                 EIVL_TS(
        #                     **{
        #                         "@xsi:type": "EIVL_TS",
        #                         "@operator": "A",
        #                         "event": {
        #                             "@code": event,
        #                         },
        #                     }
        #                 )
        #             )
        # else:
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
                    "@typeCode": "COMP",
                    "@inversionInd": True,
                    "substanceAdministration": {
                        "@classCode": "SBADM",
                        "@moodCode": "EVN",
                        "templateId": [{"@root": "2.16.840.1.113883.10.20.22.4.147"}],
                        "code": {
                            "@code": "76662-6",
                            "@codeSystem": "2.16.840.1.113883.6.1",
                        },
                        "text": dose.text,
                    },
                }
            )
        )
    # find effective time entry with operator of low

    low_time = [
        et.value
        for et in substance_administration.effectiveTime
        if getattr(et, "operator", None) == "low"
    ]
    high_time = [
        et.value
        for et in substance_administration.effectiveTime
        if getattr(et, "operator", None) == "high"
    ]
    med_name = (
        substance_administration.consumable.manufacturedProduct.manufacturedMaterial.code.displayName
    )

    # check for prescriping agency and last issued date extensions
    for ext in entry.extension:
        # print(ext.url)
        if (
            ext.url
            == "https://fhir.nhs.uk/STU3/StructureDefinition/Extension-CareConnect-GPC-PrescribingAgency-1"
        ):
            prescribing_agency = ext.valueCodeableConcept.coding[0].display
        if (
            ext.url
            == "https://fhir.nhs.uk/STU3/StructureDefinition/Extension-CareConnect-GPC-LastIssuedDate-1"
        ):
            last_issued_date = readable_date(date_helper(ext.valueDateTime.isostring))

    entry_row = [
        readable_date(low_time[0]) if low_time else "",
        readable_date(high_time[0]) if high_time else "",
        entry.status if entry.status else "unknown",
        med_name,
        substance_administration.entryRelationship[0].substanceAdministration.get(
            "text", ""
        ),
        prescribing_agency if "prescribing_agency" in locals() else "",
        last_issued_date if "last_issued_date" in locals() else "",
    ]

    return EntryWithRow(
        entry={
            "substanceAdministration": substance_administration.model_dump(
                by_alias=True, exclude_none=True
            )
        },
        row=entry_row,
    )
    # print(substance_administration.entryRelationship)
    return {
        "substanceAdministration": substance_administration.model_dump(
            by_alias=True, exclude_none=True
        )
    }


def problem(entry: condition.Condition) -> EntryWithRow:
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

    problem_row = [
        readable_date(prob["act"]["effectiveTime"].get("low", {}).get("@value", "")),
        prob["act"]["statusCode"].get("@code", ""),
        observation["value"].get("@displayName", ""),
    ]

    return EntryWithRow(entry=prob, row=problem_row)


def allergy(entry: allergyintolerance.AllergyIntolerance) -> EntryWithRow:
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
    # if there is a reaction, add manifestation as entryRelationship
    if entry.reaction and entry.reaction[0].manifestation:
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

    allergy_row = [
        readable_date(all["act"]["effectiveTime"].get("low", {}).get("@value", "")),
        all["act"]["statusCode"].get("@code", ""),
        observation["participant"]["participantRole"]["playingEntity"]["code"][
            "@displayName"
        ],
    ]

    return EntryWithRow(entry=all, row=allergy_row)


def immunization_entry(entry: immunization.Immunization, index: dict) -> EntryWithRow:
    # https://build.fhir.org/ig/HL7/CDA-ccda-2.2/StructureDefinition-2.16.840.1.113883.10.20.22.2.2.1.html

    immunization_entry = SubstanceAdministration(
        templateId=templateId("2.16.840.1.113883.10.20.22.4.52", "2014-06-09"),
        id=[{"@root": entry.id}],
        statusCode={"@code": entry.status},
        effectiveTime=effective_time_helper(entry.date),
        consumable={
            "manufacturedProduct": {
                "templateId": templateId(
                    "2.16.840.1.113883.10.20.22.4.54", "2014-06-09"
                ),
                "manufacturedMaterial": {
                    "code": code_with_translations(entry.vaccineCode.coding),
                    "lotNumberText": entry.lotNumber,
                },
            }
        },
    )

    if entry.route:
        immunization_entry.route = code_with_translations(entry.route.coding)

    # return immunization_entry.model_dump(by_alias=True, exclude_none=True)
    return EntryWithRow(
        entry=immunization_entry.model_dump(by_alias=True, exclude_none=True), row=None
    )


def result(entry, index: dict) -> dict:
    """
    Entry for results section. Entries are defined by lists that contain the related type has-member indicating results groups
    """

    # check if entry is group
    if hasattr(entry, "related") and entry.related:
        organizer = ResultsOrganizer()
        organizer.code = code_with_translations(entry.code.coding)
        organizer.statusCode = {"@code": entry.status}
        performer = index.get(entry.performer[0].reference)
        organizer.author = organization_to_author(performer)
        organizer.id = [
            {
                "@root": ident.system,
                "@extension": ident.value,
            }
            for ident in entry.identifier
        ]
        effective_time = entry.issued
        components = []
        for related in entry.related:
            print(f"Related: {related.type} - {related.target.reference}")
            if related.type == "has-member":
                related_resource = index.get(related.target.reference)
                comp = ResultObservation(
                    id=[{"@root": related_resource.id}],
                    code=code_with_translations(related_resource.code.coding),
                    status={"@code": related_resource.status},
                    # effectiveDateTime=IVL_TS(value=entry.issued.isostring),
                    value=PQ(
                        **{
                            "@value": related_resource.valueQuantity.value,
                            "@unit": related_resource.valueQuantity.unit,
                        }
                    ),
                )
                if (
                    hasattr(related_resource, "interpretation")
                    and related_resource.interpretation
                ):
                    comp.interpretationCode = code_with_translations(
                        related_resource.interpretation.coding
                    )

                if related_resource.referenceRange:
                    comp.referenceRange = {"observationRange": []}
                    for range in related_resource.referenceRange:
                        if range.text:
                            comp.referenceRange["observationRange"].append(
                                {"text": range.text}
                            )
                        if range.low:
                            comp.referenceRange["observationRange"].append(
                                {
                                    "value": {
                                        "@xsi:type": "IVL_PQ",
                                        "low": {
                                            "@value": range.low.value,
                                            "@unit": related_resource.valueQuantity.unit,
                                        },
                                        "high": {
                                            "@value": range.high.value,
                                            "@unit": related_resource.valueQuantity.unit,
                                        },
                                    }
                                }
                            )
                components.append(comp)

        organizer.component = components
        # print(organizer.model_dump(by_alias=True, exclude_none=True))

        # only return groups for now
        return organizer.model_dump(by_alias=True, exclude_none=True)
