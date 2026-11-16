import logging
from typing import Any, Optional, Union

from fhirclient.models import medication as fhirmed
from fhirclient.models import medicationrequest, medicationstatement

from ..dmd import dmd_lookup
from ..dosage_lookup import mapped_dosages
from ..helpers import (
    cda_time_bound,
    clean_number,
    code_with_translations,
    effective_time_helper,
    fhir_to_cda_timestamp,
    readable_date,
    templateId,
)
from ..models.base import Act, Criterion, EntryRelationship, Precondition, SubstanceAdministration
from ..models.datatypes import CD, ED, IVL_INT, IVL_PQ, IVL_TS, IVXB_PQ, PIVL_TS, PQ, PQR, RTO_PQ_PQ
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


def _labelled_narrative(label: str, value: Union[str, list[str]]) -> dict:
    # A literal separator lives in the normal-value node, independent of pretty-print
    # whitespace. Source remainders are verbatim: existing leading whitespace is
    # additional to this separator (and collapses in ordinary narrative rendering).
    values = [value] if isinstance(value, str) else value
    return {
        "content": [
            {"@styleCode": "Bold", "#text": label},
            {"#text": f" {values[0]}"},
            # Separate wrappers preserve alternating break/value order in xmltodict.
            *[{"br": None, "content": {"#text": item}} for item in values[1:]],
        ],
    }


def _source_note_narrative(note: str) -> dict:
    for prefix in (
        "Prescriber Notes:",
        "Patient Notes:",
        "Additional Information:",
        "Transfer degraded medication text:",
    ):
        if note.startswith(prefix):
            return {**_labelled_narrative(prefix, note[len(prefix) :]), "br": None}
    return {"#text": f"{note} <br />"}


async def medication(entry: medicationstatement.MedicationStatement, index: dict) -> EntryWithRow:
    # http://www.hl7.org/ccdasearch/templates/2.16.840.1.113883.10.20.22.4.16.html

    dosage_result = mapped_dosages(entry.dosage)
    dosages = dosage_result.dosages
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

    # First occurrence owns the narrative formatting, including supplier/warning collisions.
    narrative_notes = {note: _source_note_narrative(note) for note in misc_notes}

    def append_xhuma_warning(warning: str, *, marker: str = "Xhuma:"):
        misc_notes.append(warning)
        narrative_notes.setdefault(
            warning,
            {
                "content": {"@styleCode": "Bold", "#text": marker},
                "#text": f"{warning[len(marker) :]} <br />",
            },
        )

    multiple_dosages = len(dosages) > 1
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
    source_dose = dosages[0].doseQuantity
    dose = None
    if source_dose:
        dose = PQ(value=source_dose.value, unit=source_dose.unit)
        if source_dose.code:
            dose.translation = [
                PQR(
                    value=source_dose.value,
                    code=source_dose.code,
                    codeSystem="2.16.840.1.113883.6.96",
                    originalText=source_dose.unit,
                )
            ]
        substance_administration.doseQuantity = dose

    reason = dosages[0].asNeededCodeableConcept
    if dosages[0].asNeededBoolean or reason:
        value = code_with_translations(reason.coding) if reason else None
        if value is None:
            value = CD(nullFlavor="NI")
        if reason and reason.text:
            value.originalText = reason.text
        criterion = Criterion(
            templateId=templateId("2.16.840.1.113883.10.20.22.4.25", "2014-06-09"),
            code=CD(code="ASSERTION", codeSystem="2.16.840.1.113883.5.4"),
            value=value,
        )
        substance_administration.precondition = [Precondition(criterion=criterion)]

    if dosages[0].timing:
        repeat = dosages[0].timing.repeat
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

    if dosages[0].maxDosePerPeriod:
        numerator = dosages[0].maxDosePerPeriod.numerator
        denominator = dosages[0].maxDosePerPeriod.denominator
        if numerator and denominator:
            substance_administration.maxDoseQuantity = RTO_PQ_PQ(
                numerator=PQ(value=numerator.value, unit=numerator.unit),
                denominator=PQ(value=denominator.value, unit=denominator.unit),
            )

    #   check if route is in dosage
    if dosages[0].method:
        substance_administration.routeCode = code_with_translations(dosages[0].method.coding)

    patient_instr_list = []
    text_instr_list = []

    for i, dosage in enumerate(dosages):
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

    low_time = [
        et.low.value
        for et in substance_administration.effectiveTime
        if isinstance(et, IVL_TS) and et.low and et.low.value
    ]
    high_time = [
        et.high.value
        for et in substance_administration.effectiveTime
        if isinstance(et, IVL_TS) and et.high and et.high.value
    ]
    med_name = substance_administration.consumable.manufacturedProduct.manufacturedMaterial.code.displayName

    # check if snomed code is in cache and if so add to med name
    snomed_code = substance_administration.consumable.manufacturedProduct.manufacturedMaterial.code.code
    gp_units = ["tablet", "capsule"]
    dmd_dose_warning = None
    dmd_route_warning = None

    if dose:
        unit = (dose.unit or "").lower()
        if unit in gp_units or not unit:
            # we only process doses for tablets or capsules.

            try:
                dmd_data = await dmd_lookup(int(snomed_code))
                # only process dose if a single dosage instruction
                if len(dosages) == 1:
                    if dmd_data.vpi and dose.value is not None:
                        processed_dose = dmd_data.vpi.value * dose.value

                        # clean number to remove trailing .0 if whole number
                        processed_dose = clean_number(processed_dose)

                        dose.value = processed_dose
                        dose.unit = dmd_data.vpi.unit
                        dmd_dose_warning = (
                            f"Xhuma: Dose of {processed_dose} {dmd_data.vpi.unit} automatically mapped via dm+d lookup"
                        )

                if substance_administration.routeCode:
                    if substance_administration.routeCode.displayName == "Take":
                        # take often used with capsules. replace with dmd route.
                        if dmd_data.route:
                            substance_administration.routeCode.displayName = dmd_data.route.displayName
                            substance_administration.routeCode.code = dmd_data.route.code
                            substance_administration.routeCode.codeSystem = "2.16.840.1.113883.6.96"
                            substance_administration.routeCode.codeSystemName = dmd_data.route.codeSystemName
                            # add a warning note about the route being automatically mapped
                            dmd_route_warning = (
                                f'Xhuma: Route inferred as {dmd_data.route.displayName} from dm+d because the GP source value was "Take"; '
                                "this may not represent the patient's actual route of administration. Verify during medicines reconciliation."
                            )

            except Exception as e:
                logging.error(f"Error looking up DMD data for SNOMED code {snomed_code}: {e}")

        if "- unit of product usage" in unit:
            # strip overly verbose snomed unit description to just unit
            dose.unit = dose.unit.replace("- unit of product usage", "").strip()

    # 1. dm+d mapping/inference warnings
    if dmd_dose_warning:
        append_xhuma_warning(dmd_dose_warning)
    if dmd_route_warning:
        append_xhuma_warning(dmd_route_warning)

    # 2. free-sig / freetext dosage mapping warning
    if dosage_result.freetext_mapped:
        append_xhuma_warning("Xhuma: Free-text dosage instructions automatically mapped")

    # 3. additional dosage/clinical warnings
    if multiple_dosages:
        append_xhuma_warning("Xhuma: Multiple dosage instructions found. Use caution when converting dose**")

    for index, dosage in enumerate(dosages, start=1):
        repeat = getattr(getattr(dosage, "timing", None), "repeat", None)
        warning = _event_timing_warning(repeat, dosage_number=index if multiple_dosages else None)
        if warning:
            append_xhuma_warning(warning, marker="Xhuma")

    # check for prescribing agency and last issued date extensions
    remaining_repeats = None
    prescription_information = []
    if entry.extension:
        for ext in entry.extension:
            if ext.url == "https://fhir.nhs.uk/STU3/StructureDefinition/Extension-CareConnect-GPC-PrescribingAgency-1":
                prescribing_agency = ext.valueCodeableConcept.coding[0].display
                prescription_information.append({"content": {"#text": prescribing_agency}})
            if (
                ext.url
                == "https://fhir.nhs.uk/STU3/StructureDefinition/Extension-CareConnect-GPC-MedicationStatementLastIssueDate-1"
            ):
                last_issued_date = readable_date(fhir_to_cda_timestamp(ext.valueDateTime))
                prescription_information.append(_labelled_narrative("Last issued date:", last_issued_date))

    # look for prescription type in medication request
    if based_on_request.extension:
        for ext in based_on_request.extension:
            if ext.url == "https://fhir.nhs.uk/STU3/StructureDefinition/Extension-CareConnect-GPC-PrescriptionType-1":
                prescription_type = ext.valueCodeableConcept.coding[0].display
            if (
                ext.url
                == "https://fhir.nhs.uk/STU3/StructureDefinition/Extension-CareConnect-GPC-MedicationRepeatInformation-1"
            ):
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
                    elif i.url == "numberOfRepeatPrescriptionsIssued":
                        repeats_issued = val
                if repeats_allowed is not None and repeats_issued is not None:
                    remaining_repeats = repeats_allowed - repeats_issued
                    prescription_information.append(
                        {"content": {"#text": f"Prescription {repeats_issued} of {repeats_allowed} allowed repeats."}}
                    )
            if (
                ext.url
                == "https://fhir.nhs.uk/STU3/StructureDefinition/Extension-CareConnect-GPC-MedicationStatusReason-1"
            ):
                for i in ext.extension:
                    if i.url == "statusReason":
                        status_reason = i.valueCodeableConcept.text
                        status_note = f"Medication status reason: {status_reason}"
                        misc_notes.append(status_note)
                        narrative_notes.setdefault(
                            status_note, {**_labelled_narrative("Medication status reason:", status_reason), "br": None}
                        )

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
        prescription_information.append(_labelled_narrative("Issued quantity:", f"{quantity.value} {unit}"))

    instructions = []
    if text_instr_list:
        instructions.append(_labelled_narrative("Instructions:", text_instr_list))
    if patient_instr_list:
        instructions.append(_labelled_narrative("Patient Instructions:", patient_instr_list))
    # Breaks follow child content, never #text (which xmltodict emits last).
    for lines in (instructions, prescription_information):
        for line in lines[:-1]:
            line["br"] = None
    # use dict.fromkeys to avoid duplicates while preserving chronological insertion order
    misc_notes = list(dict.fromkeys(misc_notes))

    # CRITICAL: Epic's C-CDA parser swallows text preceding a <br/> tag in structured `xmlText` nodes.
    # We must use standard newlines (\n) for the machine-readable `xmlText`, while preserving the
    # HTML <br /> tags exclusively for the narrative `entry_row` table view.
    misc_notes_text = [narrative_notes.get(note, {"#text": f"{note} <br />"}) for note in misc_notes if note]
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
                IVL_TS(high=cda_time_bound(based_on_request.dispenseRequest.validityPeriod.end))
            ]

        if remaining_repeats is not None:
            supply_order.substanceAdministration.repeatNumber = IVL_INT(value=remaining_repeats)
        substance_administration.entryRelationship.append(supply_order)

    entry_row = [
        readable_date(low_time[0]) if low_time else "",
        readable_date(high_time[0]) if high_time else "",
        entry.status if entry.status else "unknown",
        prescription_type if "prescription_type" in locals() else "",
        {"content": {"@styleCode": "Bold", "#text": med_name}},
        {"content": instructions},
        {"content": misc_notes_text},
        {"content": prescription_information},
    ]

    return EntryWithRow(
        entry={"substanceAdministration": substance_administration.model_dump(by_alias=True, exclude_none=True)},
        row=entry_row,
    )
