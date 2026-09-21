import logging
from typing import Any, Optional, Union

from fhirclient.models import medication as fhirmed
from fhirclient.models import medicationrequest, medicationstatement

from ..dmd import dmd_lookup
from ..helpers import (
    clean_number,
    code_with_translations,
    date_helper,
    effective_time_helper,
    readable_date,
    templateId,
)
from ..models.base import Act, EntryRelationship, SubstanceAdministration
from ..models.datatypes import CD, ED, IVL_INT, IVL_PQ, IVL_TS, IVXB_PQ, PIVL_TS, PQ
from .types import EntryWithRow

# http://hl7.org/fhir/ValueSet/event-timing|4.0.1
EVENT_TIMING_LABELS = {
    "MORN": "Morning",
    "MORN.early": "Early morning",
    "MORN.late": "Late morning",
    "NOON": "Noon",
    "AFT": "Afternoon",
    "AFT.early": "Early afternoon",
    "AFT.late": "Late afternoon",
    "EVE": "Evening",
    "EVE.early": "Early evening",
    "EVE.late": "Late evening",
    "NIGHT": "Night",
    "PHS": "After sleep",
    "HS": "Before sleep",
    "WAKE": "Upon waking",
    "C": "At a meal",
    "CM": "At breakfast",
    "CD": "At lunch",
    "CV": "At dinner",
    "AC": "Before a meal",
    "ACM": "Before breakfast",
    "ACD": "Before lunch",
    "ACV": "Before dinner",
    "PC": "After a meal",
    "PCM": "After breakfast",
    "PCD": "After lunch",
    "PCV": "After dinner",
}


def _event_timing_warning(repeat: Any, dosage_number: Optional[int] = None) -> Optional[str]:
    when = getattr(repeat, "when", None)
    if not when:
        return None

    timings = []
    for code in when:
        label = EVENT_TIMING_LABELS.get(code)
        timings.append(f"{label} ({code})" if label else f"Unknown event ({code})")

    dosage_label = f" for dosage {dosage_number}" if dosage_number is not None else ""
    warning = f"Xhuma warning: Event-based medication timing{dosage_label}: "
    warning += "; ".join(timings)

    offset = getattr(repeat, "offset", None)
    if offset is not None:
        warning += f". Offset: {offset} minutes"

    return f"{warning}."


def _cda_period_from_repeat(repeat: Any) -> Optional[Union[PQ, IVL_PQ]]:
    """Convert a FHIR Timing.repeat into a C-CDA periodic interval."""
    period = getattr(repeat, "period", None)
    if period is None:
        return None

    frequency = getattr(repeat, "frequency", None)
    frequency_max = getattr(repeat, "frequencyMax", None)
    period_max = getattr(repeat, "periodMax", None)
    period_unit = getattr(repeat, "periodUnit", None)

    # FHIR defines a missing frequency as one occurrence per period.
    frequency = frequency if frequency is not None else 1

    if frequency <= 0 or (frequency_max is not None and frequency_max <= 0):
        return None

    dose_period = period / frequency
    if frequency_max is None and period_max is None:
        return PQ(**{"@value": dose_period, "@unit": period_unit})

    # A higher frequency shortens the interval, whereas a higher period lengthens
    # it. Using the outer values also handles schedules that contain both maxima.
    shortest_period = period / (frequency_max or frequency)
    longest_period = (period_max if period_max is not None else period) / frequency
    low_period, high_period = sorted((shortest_period, longest_period))

    return IVL_PQ(
        low=IVXB_PQ(**{"@value": low_period, "@unit": period_unit}),
        high=IVXB_PQ(**{"@value": high_period, "@unit": period_unit}),
    )


async def medication(entry: medicationstatement.MedicationStatement, index: dict) -> EntryWithRow:
    # http://www.hl7.org/ccdasearch/templates/2.16.840.1.113883.10.20.22.4.16.html

    referenced_med: fhirmed.Medication = index[entry.medicationReference.reference]
    based_on_request: medicationrequest.MedicationRequest = index[entry.basedOn[0].reference]
    raw_notes = []
    if based_on_request.note:
        raw_notes.extend(based_on_request.note)
    if entry.note:
        raw_notes.extend(entry.note)

    misc_notes = []
    for n in raw_notes:
        if hasattr(n, "text") and n.text:
            misc_notes.append(n.text)

    # Remove exact duplicates to avoid mutual annihilation during substring checks
    unique_notes = list(dict.fromkeys(misc_notes))

    # check if any of the notes are contained in another one (e.g., preceded by "Prescriber Notes:")
    # if so delete the contained note
    misc_notes = [
        text
        for text in unique_notes
        if not any(text in other_text and text != other_text for other_text in unique_notes)
    ]

    # append entry text if snomed code is 196421000000109
    for code in referenced_med.code.coding:
        if code.code == "196421000000109":
            misc_notes.append(f"Transfer degraded medication text: {referenced_med.code.text}")

    multiple_dosages = len(entry.dosage) > 1
    for index, dosage in enumerate(entry.dosage, start=1):
        repeat = getattr(getattr(dosage, "timing", None), "repeat", None)
        warning = _event_timing_warning(repeat, dosage_number=index if multiple_dosages else None)
        if warning:
            misc_notes.append(warning)
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
                "templateId": templateId(root="2.16.840.1.113883.10.20.22.4.23", extension="2014-06-09"),
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
            substance_administration.doseQuantity["@unit"] = entry.dosage[0].doseQuantity.unit

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
                "templateId": templateId(root="2.16.840.1.113883.10.20.22.4.25", extension="2014-06-09"),
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
                "@displayName": entry.dosage[0].asNeededCodeableConcept.coding[0].display,
                "@codeSystemName": entry.dosage[0].asNeededCodeableConcept.coding[0].value,
            }
        else:
            # if no asNeededCodeableConcept, use NI
            substance_administration.precondition["criterion"]["value"] = {
                "@xsi:type": "CD",
                "@nullFlavor": "NI",
            }

    if entry.dosage[0].timing:
        repeat = entry.dosage[0].timing.repeat
        frequency = getattr(repeat, "frequency", None)
        frequency_max = getattr(repeat, "frequencyMax", None)
        pivl = PIVL_TS(
            **{
                "@xsi:type": "PIVL_TS",
                "@operator": "A",
                "@institutionSpecified": ("true" if frequency is not None or frequency_max is not None else None),
            }
        )

        pivl.period = _cda_period_from_repeat(repeat)

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
        substance_administration.routeCode = code_with_translations(entry.dosage[0].method.coding)

    patient_instr_list = []
    text_instr_list = []

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
            templateId=templateId(root="2.16.840.1.113883.10.20.22.4.200", extension="2014-06-09"),
            code=CD(
                code="422037009",
                codeSystem="2.16.840.1.113883.6.96",
                codeSystemName="http://snomed.info/sct",
            ),
            text=ED(xmlText=combined_patient_instructions),
        )
        substance_administration.entryRelationship.append(instruction_entry)
    # find effective time entry with operator of low

    low_time = [et.value for et in substance_administration.effectiveTime if getattr(et, "operator", None) == "low"]
    high_time = [et.value for et in substance_administration.effectiveTime if getattr(et, "operator", None) == "high"]
    med_name = substance_administration.consumable.manufacturedProduct.manufacturedMaterial.code.displayName

    # check if snomed code is in cache and if so add to med name
    snomed_code = substance_administration.consumable.manufacturedProduct.manufacturedMaterial.code.code
    # print(substance_administration.doseQuantity)
    gp_units = ["tablet", "capsule"]
    unit = (
        substance_administration.doseQuantity.get("@unit", "").lower() if substance_administration.doseQuantity else ""
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
                        processed_dose = dmd_data.vpi.value * substance_administration.doseQuantity["@value"]

                        # clean number to remove trailing .0 if whole number
                        processed_dose = clean_number(processed_dose)

                        substance_administration.doseQuantity["@value"] = processed_dose
                        substance_administration.doseQuantity["@unit"] = dmd_data.vpi.unit
                        warning_text = (
                            f"Xhuma: Dose of {processed_dose} {dmd_data.vpi.unit} automatically mapped via dm+d lookup"
                        )
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
                            substance_administration.routeCode.displayName = dmd_data.route.displayName
                            substance_administration.routeCode.code = dmd_data.route.code
                            substance_administration.routeCode.codeSystem = "2.16.840.1.113883.6.96"
                            # substance_administration.routeCode.codeSystem = (
                            #     "2.16.840.1.113883.3.26.1.1"
                            # )
                            substance_administration.routeCode.codeSystemName = dmd_data.route.codeSystemName
                            # route_translation = CD()
                            # route_translation["@code"] = dmd_data.route.code
                            # route_translation.codeSystem = "2.16.840.1.113883.3.26.1.1"
                            # substance_administration.routeCode.translation = (
                            #     route_translation
                            # )

            except Exception as e:
                logging.error(f"Error looking up DMD data for SNOMED code {snomed_code}: {e}")
                print(f"Error looking up DMD data for SNOMED code {snomed_code}: {e}")
                pass

        if "- unit of product usage" in unit:
            # strip overly verbose snomed unit description to just unit
            substance_administration.doseQuantity["@unit"] = (
                substance_administration.doseQuantity["@unit"].replace("- unit of product usage", "").strip()
            )

    # check for prescribing agency and last issued date extensions
    remaining_repeats = None
    prescription_information = []
    if entry.extension:
        for ext in entry.extension:
            # print(ext.url)
            if ext.url == "https://fhir.nhs.uk/STU3/StructureDefinition/Extension-CareConnect-GPC-PrescribingAgency-1":
                prescribing_agency = ext.valueCodeableConcept.coding[0].display
                prescription_information.append(prescribing_agency)
            if (
                ext.url
                == "https://fhir.nhs.uk/STU3/StructureDefinition/Extension-CareConnect-GPC-MedicationStatementLastIssueDate-1"
            ):
                last_issued_date = readable_date(date_helper(ext.valueDateTime.isostring))
                prescription_information.append(f"Last issued date: {last_issued_date}")

    # look for prescription type in medication request
    if based_on_request.extension:
        for ext in based_on_request.extension:
            if ext.url == "https://fhir.nhs.uk/STU3/StructureDefinition/Extension-CareConnect-GPC-PrescriptionType-1":
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
        prescription_information.append(issued_quantity)

    # add br tags to prescription information with a join
    prescription_information = "<br />".join(prescription_information) if prescription_information else ""
    # prescription_information = [f"{info} <br />" for info in prescription_information]

    patient_instructions = "Patient Instructions: " + "<br />".join(patient_instr_list) if patient_instr_list else ""
    text_instructions = " Instructions: " + "<br />".join(text_instr_list) if text_instr_list else ""
    # use dict.fromkeys to avoid duplicates while preserving chronological insertion order
    misc_notes = list(dict.fromkeys(misc_notes))

    # CRITICAL: Epic's C-CDA parser swallows text preceding a <br/> tag in structured `xmlText` nodes.
    # We must use standard newlines (\n) for the machine-readable `xmlText`, while preserving the
    # HTML <br /> tags exclusively for the narrative `entry_row` table view.
    misc_notes_text = [f"{note} <br />" for note in misc_notes if note]
    structured_notes_text = "\n".join(misc_notes) if misc_notes else ""

    comment_activity = EntryRelationship()
    comment_activity.act = Act(
        code=CD(
            code="48767-8",
        ),
        text=ED(
            xmlText=structured_notes_text,
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
                IVL_TS(high={"@value": date_helper(based_on_request.dispenseRequest.validityPeriod.end.isostring)})
            ]

        if remaining_repeats is not None:
            supply_order.substanceAdministration.repeatNumber = IVL_INT(value=remaining_repeats)
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
        entry={"substanceAdministration": substance_administration.model_dump(by_alias=True, exclude_none=True)},
        row=entry_row,
    )
