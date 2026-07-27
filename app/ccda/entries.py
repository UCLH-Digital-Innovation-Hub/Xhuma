import logging
import uuid
from dataclasses import dataclass
from typing import Any, List, Optional, Union

from fhirclient.models import allergyintolerance, condition, immunization
from fhirclient.models import medication as fhirmed
from fhirclient.models import medicationrequest, medicationstatement

from .dmd import dmd_lookup
from .helpers import (
    clean_number,
    code_with_translations,
    date_helper,
    effective_time_helper,
    organization_to_author,
    readable_date,
    templateId,
)
from .models.base import (
    EntryRelationship,
    ResultObservation,
    ResultsOrganizer,
    SubstanceAdministration,
    Act,
)
from .models.datatypes import (
    CD,
    ED,
    IVL_INT,
    IVL_TS,
    PIVL_TS,
    PQ,
    IVL_PQ,
    IVXB_PQ,
    SXCM_TS,
    IVXB_TS,
)

Cell = str
Row = List[Cell]


@dataclass(frozen=True)
class EntryWithRow:
    entry: Any  # C-CDA entry section
    row: Optional[Row]  # row data for summary table in section


async def medication(
    entry: medicationstatement.MedicationStatement, index: dict
) -> EntryWithRow:
    # http://www.hl7.org/ccdasearch/templates/2.16.840.1.113883.10.20.22.4.16.html

    referenced_med: fhirmed.Medication = index[entry.medicationReference.reference]
    based_on_request: medicationrequest.MedicationRequest = index[
        entry.basedOn[0].reference
    ]
    misc_notes = based_on_request.note if based_on_request.note else []
    misc_notes += entry.note if entry.note else []
    # check if any of the notes are container in another one preceeded by "Prescriber Notes:
    # if so delete the contained note"
    misc_notes = [
        note
        for note in misc_notes
        if not any(
            note.text in other_note.text and note != other_note
            for other_note in misc_notes
        )
    ]
    # append entry text if snomed code is 196421000000109
    for code in referenced_med.code.coding:
        if code.code == "196421000000109":
            misc_notes.append(
                f"Transfer degraded medication text: {referenced_med.code.text}"
            )

    for i, note in enumerate(misc_notes):
        if hasattr(note, "text"):
            # replace note with just the text
            misc_notes[i] = note.text
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
        # TODO use proper PQ model instead of dict
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
    # check if dosage has as needed boolean of true

    if entry.dosage[0].asNeededBoolean:
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

    if entry.dosage[0].timing:
        pivl = PIVL_TS(
            **{
                "@xsi:type": "PIVL_TS",
                "@operator": "A",
                "@institutionSpecified": (
                    "true" if entry.dosage[0].timing.repeat.frequency else None
                ),
            }
        )

        # check frequency has period and frequency
        repeat = entry.dosage[0].timing.repeat
        period = getattr(repeat, "period", None)
        frequency = getattr(repeat, "frequency", None)
        period_max = getattr(repeat, "periodMax", None)
        period_unit = getattr(repeat, "periodUnit", None)
        # make sure frequency and period are not None
        if period is not None and frequency is not None:
            # if period and frequency:
            if period_max is not None:
                low_period = period / frequency
                high_period = period_max / frequency
                pivl.period = IVL_PQ(
                    low=IVXB_PQ(**{"@value": low_period, "@unit": period_unit}),
                    high=IVXB_PQ(**{"@value": high_period, "@unit": period_unit}),
                )

            else:
                # frequency is the occurrence per period. C-CDA has a single period between doses hence division
                dose_period = period / frequency

                pivl.period = {
                    "@value": dose_period,
                    "@unit": period_unit,
                }

        substance_administration.effectiveTime.append(pivl)

    if entry.dosage[0].maxDosePerPeriod:
        numerator = entry.dosage[0].maxDosePerPeriod.numerator
        denominator = entry.dosage[0].maxDosePerPeriod.denominator
        if numerator and denominator:
            try:
                substance_administration.maxDoseQuantity = {
                    "@xsi:type": "RTO_PQ_PQ",
                    "numerator": {
                        "@value": numerator.value,
                        "@unit": numerator.unit,
                    },
                    "denominator": {
                        "@value": denominator.value,
                        "@unit": denominator.unit,
                    },
                }
            except Exception as e:
                logging.error(f"Error processing maxDosePerPeriod: {e}")
                pass

    #   check if route is in dosage
    if entry.dosage[0].method:
        substance_administration.routeCode = code_with_translations(
            entry.dosage[0].method.coding
        )

    patient_instr_list = []
    text_instr_list = []

    multiple_dosages = len(entry.dosage) > 1

    for i, dosage in enumerate(entry.dosage):
        prefix = f"{i + 1}. " if multiple_dosages else ""

        if dosage.patientInstruction:
            patient_instr_list.append(f"{prefix}{dosage.patientInstruction}")

        if dosage.text:
            text_instr_list.append(f"{prefix}{dosage.text}")

    if text_instr_list:
        combined_text = "; ".join(text_instr_list)
        dosage_entry = EntryRelationship(**{"@typeCode": "COMP", "@inversionInd": True})
        dosage_entry.substanceAdministration = SubstanceAdministration(
            moodCode="EVN",
            typeCode="COMP",
            templateId=templateId(
                root="2.16.840.1.113883.10.20.22.4.147",
                extension="2014-06-09",
            ),
            code=CD(
                code="76662-6",
                codeSystem="2.16.840.1.113883.6.1",
                displayName="Dosage instructions",
            ),
            text=combined_text,
        )
        substance_administration.entryRelationship.append(dosage_entry)

    if patient_instr_list:
        combined_patient_instructions = "; ".join(patient_instr_list)
        instruction_entry = EntryRelationship()
        instruction_entry.act = Act(
            moodCode="INT",
            templateId=templateId(
                root="2.16.840.1.113883.10.20.22.4.200", extension="2014-06-09"
            ),
            code=CD(
                code="422037009",
                codeSystem="2.16.840.1.113883.6.96",
                codeSystemName="http://snomed.info/sct",
            ),
            text=ED(xmlText=combined_patient_instructions),
        )
        substance_administration.entryRelationship.append(instruction_entry)
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
    med_name = substance_administration.consumable.manufacturedProduct.manufacturedMaterial.code.displayName

    # check if snomed code is in cache and if so add to med name
    snomed_code = substance_administration.consumable.manufacturedProduct.manufacturedMaterial.code.code
    # print(substance_administration.doseQuantity)
    gp_units = ["tablet", "capsule"]
    unit = (
        substance_administration.doseQuantity.get("@unit", "").lower()
        if substance_administration.doseQuantity
        else ""
    )

    if substance_administration.doseQuantity:
        blank_unit = not substance_administration.doseQuantity.get("@unit")
        if unit in gp_units or blank_unit:
            # we only process doses for tablets or capsules.

            try:
                dmd_data = await dmd_lookup(int(snomed_code))
                # only process dose if a single dosage instruction
                if len(entry.dosage) == 1:
                    if dmd_data.vpi and substance_administration.doseQuantity:
                        processed_dose = (
                            dmd_data.vpi.value
                            * substance_administration.doseQuantity["@value"]
                        )

                        # clean number to remove trailing .0 if whole number
                        processed_dose = clean_number(processed_dose)

                        substance_administration.doseQuantity["@value"] = processed_dose
                        substance_administration.doseQuantity["@unit"] = (
                            dmd_data.vpi.unit
                        )
                        warning_text = f"Xhuma: Dose of {processed_dose} {dmd_data.vpi.unit} automatically mapped via dm+d lookup"
                        # print(warning_text)
                        misc_notes.append(warning_text)

                elif len(entry.dosage) > 1:
                    # multiple dosage instrutions so add warning to medication name instead of processing dose
                    warning_text = "Xhuma: Multiple dosage instructions found. Use caution when converting dose**"
                    misc_notes.append(warning_text)

                if substance_administration.routeCode:
                    if substance_administration.routeCode.displayName == "Take":
                        # take often used with capsules. replace with dmd route.
                        if dmd_data.route:
                            substance_administration.routeCode.displayName = (
                                dmd_data.route.displayName
                            )
                            substance_administration.routeCode.code = (
                                dmd_data.route.code
                            )
                            substance_administration.routeCode.codeSystem = (
                                "2.16.840.1.113883.6.96"
                            )
                            # substance_administration.routeCode.codeSystem = (
                            #     "2.16.840.1.113883.3.26.1.1"
                            # )
                            substance_administration.routeCode.codeSystemName = (
                                dmd_data.route.codeSystemName
                            )
                            # route_translation = CD()
                            # route_translation["@code"] = dmd_data.route.code
                            # route_translation.codeSystem = "2.16.840.1.113883.3.26.1.1"
                            # substance_administration.routeCode.translation = (
                            #     route_translation
                            # )

            except Exception as e:
                logging.error(
                    f"Error looking up DMD data for SNOMED code {snomed_code}: {e}"
                )
                print(f"Error looking up DMD data for SNOMED code {snomed_code}: {e}")
                pass

        if "- unit of product usage" in unit:
            # strip overly verbose snomed unit description to just unit
            substance_administration.doseQuantity["@unit"] = (
                substance_administration.doseQuantity["@unit"]
                .replace("- unit of product usage", "")
                .strip()
            )

    # check for prescribing agency and last issued date extensions
    remaining_repeats = None
    prescription_information = []
    if entry.extension:
        for ext in entry.extension:
            # print(ext.url)
            if (
                ext.url
                == "https://fhir.nhs.uk/STU3/StructureDefinition/Extension-CareConnect-GPC-PrescribingAgency-1"
            ):
                prescribing_agency = ext.valueCodeableConcept.coding[0].display
                prescription_information.append(prescribing_agency)
            if (
                ext.url
                == "https://fhir.nhs.uk/STU3/StructureDefinition/Extension-CareConnect-GPC-MedicationStatementLastIssueDate-1"
            ):
                last_issued_date = readable_date(
                    date_helper(ext.valueDateTime.isostring)
                )
                prescription_information.append(f"Last issued date: {last_issued_date}")

    # look for prescription type in medication request
    if based_on_request.extension:
        for ext in based_on_request.extension:
            if (
                ext.url
                == "https://fhir.nhs.uk/STU3/StructureDefinition/Extension-CareConnect-GPC-PrescriptionType-1"
            ):
                prescription_type = ext.valueCodeableConcept.coding[0].display
            if (
                ext.url
                == "https://fhir.nhs.uk/STU3/StructureDefinition/Extension-CareConnect-GPC-MedicationRepeatInformation-1"
            ):
                # print("Medication repeat information extension found")
                repeats_allowed = None
                repeats_issued = None
                for i in ext.extension:
                    val = getattr(i, "valuePositiveInt", None)
                    if val is None:
                        val = getattr(i, "valueUnsignedInt", None)
                    if val is None:
                        val = getattr(i, "valueInteger", None)

                    if i.url == "numberOfRepeatPrescriptionsAllowed":
                        repeats_allowed = val
                        # print(repeats_allowed)
                    elif i.url == "numberOfRepeatPrescriptionsIssued":
                        repeats_issued = val
                        # print(f"Repeats Issued:{repeats_issued}")
                if repeats_allowed is not None and repeats_issued is not None:
                    remaining_repeats = repeats_allowed - repeats_issued
                    misc_notes.append(
                        f"Prescription {repeats_issued} of {repeats_allowed} allowed repeats."
                    )
                    prescription_information.append(
                        f"Prescription {repeats_issued} of {repeats_allowed} allowed repeats."
                    )
            if (
                ext.url
                == "https://fhir.nhs.uk/STU3/StructureDefinition/Extension-CareConnect-GPC-MedicationStatusReason-1"
            ):
                for i in ext.extension:
                    if i.url == "statusReason":
                        status_reason = i.valueCodeableConcept.text
                        misc_notes.append(f"Medication status reason: {status_reason}")

    # process issued quantity from based_on_request
    if based_on_request.dispenseRequest and based_on_request.dispenseRequest.quantity:
        quantity = based_on_request.dispenseRequest.quantity
        unit = None
        if quantity.unit:
            unit = quantity.unit
        # else look for dose quanity extension
        elif quantity.extension:
            for ext in quantity.extension:
                if (
                    ext.url
                    == "https://fhir.nhs.uk/STU3/StructureDefinition/Extension-CareConnect-GPC-MedicationQuantityText-1"
                ):
                    unit = ext.valueString
        issued_quantity = f"Issued quantity: {quantity.value} {unit}"
        misc_notes.append(issued_quantity)
        prescription_information.append(issued_quantity)

    # add br tags to prescription information with a join
    prescription_information = (
        "<br />".join(prescription_information) if prescription_information else ""
    )
    # prescription_information = [f"{info} <br />" for info in prescription_information]

    patient_instructions = (
        "Patient Instructions: " + "<br />".join(patient_instr_list)
        if patient_instr_list
        else ""
    )
    text_instructions = (
        " Instructions: " + "<br />".join(text_instr_list) if text_instr_list else ""
    )
    # make misc notes a set to avoid duplicates
    misc_notes = list(set(misc_notes))

    misc_notes_text = [f"{note} <br />" for note in misc_notes if note]

    # misc_notes_text = {[f"{note} \n " for note in misc_notes if note]}
    # print(f"Misc notes text: {''.join(misc_notes_text)}")
    comment_activity = EntryRelationship()
    comment_activity.act = Act(
        code=CD(
            code="48767-8",
        ),
        text=ED(
            xmlText="".join(misc_notes_text) if misc_notes_text else "",
        ),
    )
    substance_administration.entryRelationship.append(comment_activity)

    # add dispensing  request
    if based_on_request.dispenseRequest:
        supply_order = EntryRelationship(**{"@typeCode": "REFR"})
        supply_order.substanceAdministration = SubstanceAdministration()
        supply_order.substanceAdministration.moodCode = "EVN"
        if based_on_request.dispenseRequest.validityPeriod.end:
            supply_order.substanceAdministration.effectiveTime = [
                IVL_TS(
                    high={
                        "@value": date_helper(
                            based_on_request.dispenseRequest.validityPeriod.end.isostring
                        )
                    }
                )
            ]

        if remaining_repeats is not None:
            supply_order.substanceAdministration.repeatNumber = IVL_INT(
                value=remaining_repeats
            )
        substance_administration.entryRelationship.append(supply_order)

    # entry_row = [
    #     readable_date(low_time[0]) if low_time else "",
    #     readable_date(high_time[0]) if high_time else "",
    #     entry.status if entry.status else "unknown",
    #     prescription_type if "prescription_type" in locals() else "",
    #     med_name,
    #     f"{text_instructions}<br />{patient_instructions}",
    #     {"BR": misc_notes_text},
    #     prescribing_agency if "prescribing_agency" in locals() else "",
    #     last_issued_date if "last_issued_date" in locals() else "",
    # ]
    entry_row = [
        readable_date(low_time[0]) if low_time else "",
        readable_date(high_time[0]) if high_time else "",
        entry.status if entry.status else "unknown",
        prescription_type if "prescription_type" in locals() else "",
        med_name,
        f"{text_instructions}<br />{patient_instructions}",
        {"BR": misc_notes_text},
        prescription_information,
    ]

    return EntryWithRow(
        entry={
            "substanceAdministration": substance_administration.model_dump(
                by_alias=True, exclude_none=True
            )
        },
        row=entry_row,
    )


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
    prob["act"]["code"] = {"@code": "CONC", "@codeSystem": "2.16.840.1.113883.5.6"}

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
                "code": code_with_translations(entry.code.coding).model_dump(
                    by_alias=True, exclude_none=True
                ),
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
                "templateId": templateId(
                    "2.16.840.1.113883.10.20.22.4.9", "2014-06-09"
                ),
                "id": {"@root": uuid.uuid4()},
                "code": {"@code": "ASSERTION", "@codeSystem": "2.16.840.1.113883.5.4"},
                "effectiveTime": {
                    "low": {"@value": date_helper(entry.assertedDate.isostring)}
                },
                "value": {
                    "@xsi:type": "CD",
                    "@code": entry.reaction[0].manifestation[0].coding[0].code,
                    "@displayName": entry.reaction[0]
                    .manifestation[0]
                    .coding[0]
                    .display,
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
        effectiveTime=[SXCM_TS(value=date_helper(entry.date.isostring))]
        if entry.date
        else [],
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
        entryRelationship=[],
    )

    if entry.route:
        immunization_entry.route = code_with_translations(entry.route.coding)

    # Parse additional information
    misc_notes = []
    if hasattr(entry, "note") and entry.note:
        for note in entry.note:
            if hasattr(note, "text") and note.text:
                misc_notes.append(note.text)
            elif isinstance(note, str):
                misc_notes.append(note)

    if hasattr(entry, "explanation") and entry.explanation:
        if hasattr(entry.explanation, "reason") and entry.explanation.reason:
            for reason in entry.explanation.reason:
                if hasattr(reason, "text") and reason.text:
                    misc_notes.append(f"Reason: {reason.text}")
                elif (
                    hasattr(reason, "coding")
                    and reason.coding
                    and reason.coding[0].display
                ):
                    misc_notes.append(f"Reason: {reason.coding[0].display}")
        if (
            hasattr(entry.explanation, "reasonNotGiven")
            and entry.explanation.reasonNotGiven
        ):
            for reason in entry.explanation.reasonNotGiven:
                if hasattr(reason, "text") and reason.text:
                    misc_notes.append(f"Reason not given: {reason.text}")
                elif (
                    hasattr(reason, "coding")
                    and reason.coding
                    and reason.coding[0].display
                ):
                    misc_notes.append(f"Reason not given: {reason.coding[0].display}")

    if misc_notes:
        misc_notes_text = [f"{note} <br />" for note in misc_notes if note]
        comment_activity = EntryRelationship()
        comment_activity.act = {
            "code": {
                "@code": "48767-8",
            },
            "text": {"@xsi:type": "ED", "xmlText": {"BR": misc_notes_text}},
        }
        immunization_entry.entryRelationship.append(comment_activity)

    date_val = readable_date(date_helper(entry.date.isostring)) if entry.date else ""
    vaccine_val = (
        entry.vaccineCode.coding[0].display
        if (entry.vaccineCode and entry.vaccineCode.coding)
        else ""
    )
    if misc_notes:
        vaccine_val = f"{vaccine_val}<br />Notes: " + "<br />".join(misc_notes)

    lot_val = entry.lotNumber if entry.lotNumber else ""
    status_val = entry.status if entry.status else ""

    immunization_row = [date_val, vaccine_val, lot_val, status_val]

    return EntryWithRow(
        entry=immunization_entry.model_dump(by_alias=True, exclude_none=True),
        row=immunization_row,
    )


def observation_entry(
    entry, index: dict, section_name: Union[str, int]
) -> EntryWithRow:
    from .models.base import Observation

    obs = Observation(
        templateId=templateId("2.16.840.1.113883.10.20.22.4.2", "2015-08-01"),
        id=[{"@root": entry.id}],
        statusCode={"@code": "completed"},
    )

    if hasattr(entry, "code") and entry.code:
        obs.code = code_with_translations(entry.code.coding)

    # Map value if present
    if hasattr(entry, "valueCodeableConcept") and entry.valueCodeableConcept:
        obs.value = code_with_translations(entry.valueCodeableConcept.coding)
    elif hasattr(entry, "valueString") and entry.valueString:
        obs.value = {"@xsi:type": "ST", "#text": entry.valueString}
    elif hasattr(entry, "valueQuantity") and entry.valueQuantity:
        # TODO use formal pq model
        obs.value = {
            "@xsi:type": "PQ",
            "@value": entry.valueQuantity.value,
            "@unit": entry.valueQuantity.unit,
        }

    # Parse additional notes/comments for Observation
    obs_notes = []
    if hasattr(entry, "comment") and entry.comment:
        obs_notes.append(entry.comment)
    if hasattr(entry, "note") and entry.note:
        for note in entry.note:
            if hasattr(note, "text") and note.text:
                obs_notes.append(note.text)
            elif isinstance(note, str):
                obs_notes.append(note)

    if obs_notes:
        obs_notes_text = [f"{note} <br />" for note in obs_notes if note]
        comment_activity = EntryRelationship()
        comment_activity.act = {
            "code": {
                "@code": "48767-8",
            },
            "text": {"@xsi:type": "ED", "xmlText": {"BR": obs_notes_text}},
        }
        obs.entryRelationship = [comment_activity]

    date_val = "N/A"
    if hasattr(entry, "effectiveDateTime") and entry.effectiveDateTime:
        obs.effectiveTime = IVL_TS(
            **{"@value": date_helper(entry.effectiveDateTime.isostring)}
        )
        date_val = readable_date(date_helper(entry.effectiveDateTime.isostring))
    elif (
        hasattr(entry, "effectivePeriod")
        and entry.effectivePeriod
        and entry.effectivePeriod.start
    ):
        obs.effectiveTime = IVL_TS(
            low=IVXB_TS(
                **{"@value": date_helper(entry.effectivePeriod.start.isostring)}
            )
        )
        date_val = readable_date(date_helper(entry.effectivePeriod.start.isostring))
    elif hasattr(entry, "effectiveInstant") and entry.effectiveInstant:
        obs.effectiveTime = IVL_TS(
            **{"@value": date_helper(entry.effectiveInstant.isostring)}
        )
        date_val = readable_date(date_helper(entry.effectiveInstant.isostring))

    name_val = "N/A"
    if hasattr(entry, "code") and entry.code and entry.code.coding:
        for coding in entry.code.coding:
            if coding.display:
                name_val = coding.display
                break

    if obs_notes:
        name_val = f"{name_val}<br />Notes: " + "<br />".join(obs_notes)

    # Build row based on the specific section layout
    if section_name == "Immunisations":
        row = [date_val, name_val, "N/A", "N/A"]
    elif section_name == "Problems":
        row = [date_val, "N/A", name_val]
    elif section_name == "Allergies and adverse reactions":
        row = [date_val, "N/A", name_val, "N/A"]
    else:
        # Fallback to old behavior if a simple integer length is passed
        row = [date_val, name_val]
        row_len = (
            int(section_name)
            if isinstance(section_name, int)
            or (isinstance(section_name, str) and section_name.isdigit())
            else 3
        )
        while len(row) < row_len:
            row.append("N/A")

    return EntryWithRow(
        entry={"observation": obs.model_dump(by_alias=True, exclude_none=True)}, row=row
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
        # effective_time = entry.issued
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
                            # TODO use proper model instead of dict
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


def empty_entry(title: str) -> List[dict]:
    """Generates an empty C-CDA entry with nullFlavor="NI" for empty sections."""
    entry_id = str(uuid.uuid4())
    if title == "Allergies and adverse reactions":
        return [
            {
                "act": {
                    "@classCode": "ACT",
                    "@moodCode": "EVN",
                    "templateId": templateId(
                        "2.16.840.1.113883.10.20.22.4.30", "2015-08-01"
                    ),
                    "id": [{"@root": entry_id}],
                    "code": {"@code": "CONC", "@codeSystem": "2.16.840.1.113883.5.6"},
                    "statusCode": {"@code": "active"},
                    "effectiveTime": {"@nullFlavor": "NI"},
                    "entryRelationship": [
                        {
                            "@typeCode": "SUBJ",
                            "observation": {
                                "@classCode": "OBS",
                                "@moodCode": "EVN",
                                "templateId": templateId(
                                    "2.16.840.1.113883.10.20.22.4.7", "2014-06-09"
                                ),
                                "id": [{"@root": str(uuid.uuid4())}],
                                "code": {
                                    "@code": "ASSERTION",
                                    "@codeSystem": "2.16.840.1.113883.5.4",
                                },
                                "statusCode": {"@code": "completed"},
                                "value": {"@xsi:type": "CD", "@nullFlavor": "NI"},
                            },
                        }
                    ],
                }
            }
        ]
    elif title == "Problems":
        return [
            {
                "act": {
                    "@classCode": "ACT",
                    "@moodCode": "EVN",
                    "templateId": templateId(
                        "2.16.840.1.113883.10.20.22.4.3", "2015-08-01"
                    ),
                    "id": [{"@root": entry_id}],
                    "code": {"@code": "CONC", "@codeSystem": "2.16.840.1.113883.5.6"},
                    "statusCode": {"@code": "active"},
                    "effectiveTime": {"@nullFlavor": "NI"},
                    "entryRelationship": [
                        {
                            "@typeCode": "SUBJ",
                            "observation": {
                                "@classCode": "OBS",
                                "@moodCode": "EVN",
                                "templateId": templateId(
                                    "2.16.840.1.113883.10.20.22.4.4", "2015-08-01"
                                ),
                                "id": [{"@root": str(uuid.uuid4())}],
                                "code": [
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
                                ],
                                "statusCode": {"@code": "completed"},
                                "effectiveTime": {"@nullFlavor": "NI"},
                                "value": {"@xsi:type": "CD", "@nullFlavor": "NI"},
                            },
                        }
                    ],
                }
            }
        ]
    elif title in [
        "Active Medications",
        "Past Medications",
        "Medications and medical devices",
    ]:
        return [
            {
                "substanceAdministration": {
                    "@classCode": "SBADM",
                    "@moodCode": "EVN",
                    "templateId": templateId(
                        "2.16.840.1.113883.10.20.22.4.16", "2014-06-09"
                    ),
                    "id": [{"@root": entry_id}],
                    "statusCode": {"@code": "active"},
                    "effectiveTime": [{"@nullFlavor": "NI"}],
                    "consumable": {
                        "manufacturedProduct": {
                            "templateId": templateId(
                                root="2.16.840.1.113883.10.20.22.4.23",
                                extension="2014-06-09",
                            ),
                            "manufacturedMaterial": {"code": {"@nullFlavor": "NI"}},
                        }
                    },
                }
            }
        ]
    elif title == "Immunisations":
        return [
            {
                "substanceAdministration": {
                    "@classCode": "SBADM",
                    "@moodCode": "EVN",
                    "templateId": templateId(
                        "2.16.840.1.113883.10.20.22.4.52", "2014-06-09"
                    ),
                    "id": [{"@root": entry_id}],
                    "statusCode": {"@code": "completed"},
                    "effectiveTime": [{"@nullFlavor": "NI"}],
                    "consumable": {
                        "manufacturedProduct": {
                            "templateId": templateId(
                                "2.16.840.1.113883.10.20.22.4.54", "2014-06-09"
                            ),
                            "manufacturedMaterial": {"code": {"@nullFlavor": "NI"}},
                        }
                    },
                }
            }
        ]
    elif title in ["Investigations and results", "Vital Signs"]:
        return [
            {
                "organizer": {
                    "@classCode": "BATTERY",
                    "@moodCode": "EVN",
                    "templateId": templateId(
                        "2.16.840.1.113883.10.20.22.4.1", "2015-08-01"
                    ),
                    "id": [{"@root": entry_id}],
                    "code": {"@nullFlavor": "NI"},
                    "statusCode": {"@code": "completed"},
                    "component": {
                        "observation": {
                            "@classCode": "OBS",
                            "@moodCode": "EVN",
                            "templateId": templateId(
                                "2.16.840.1.113883.10.20.22.4.2", "2015-08-01"
                            ),
                            "id": [{"@root": str(uuid.uuid4())}],
                            "code": {"@nullFlavor": "NI"},
                            "statusCode": {"@code": "completed"},
                            "effectiveTime": {"@nullFlavor": "NI"},
                            "value": {"@xsi:type": "ANY", "@nullFlavor": "NI"},
                        }
                    },
                }
            }
        ]
    return []
