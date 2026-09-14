from fhirclient.models import immunization

from ..helpers import code_with_translations, date_helper, readable_date, templateId
from ..models.base import EntryRelationship, SubstanceAdministration
from ..models.datatypes import SXCM_TS
from .types import EntryWithRow


def immunization_entry(entry: immunization.Immunization, index: dict) -> EntryWithRow:
    # https://build.fhir.org/ig/HL7/CDA-ccda-2.2/StructureDefinition-2.16.840.1.113883.10.20.22.2.2.1.html

    immunization_entry = SubstanceAdministration(
        templateId=templateId("2.16.840.1.113883.10.20.22.4.52", "2014-06-09"),
        id=[{"@root": entry.id}],
        statusCode={"@code": entry.status},
        effectiveTime=[SXCM_TS(value=date_helper(entry.date.isostring))] if entry.date else [],
        consumable={
            "manufacturedProduct": {
                "templateId": templateId("2.16.840.1.113883.10.20.22.4.54", "2014-06-09"),
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
                elif hasattr(reason, "coding") and reason.coding and reason.coding[0].display:
                    misc_notes.append(f"Reason: {reason.coding[0].display}")
        if hasattr(entry.explanation, "reasonNotGiven") and entry.explanation.reasonNotGiven:
            for reason in entry.explanation.reasonNotGiven:
                if hasattr(reason, "text") and reason.text:
                    misc_notes.append(f"Reason not given: {reason.text}")
                elif hasattr(reason, "coding") and reason.coding and reason.coding[0].display:
                    misc_notes.append(f"Reason not given: {reason.coding[0].display}")

    if misc_notes:
        # CRITICAL: Epic's C-CDA parser swallows text preceding a <br/> tag in structured `xmlText` nodes.
        # We must use standard newlines (\n) for the machine-readable `xmlText`, while preserving the
        # HTML <br /> tags exclusively for the narrative `immunization_row` table view.
        structured_notes_text = "\n".join(misc_notes)

        comment_activity = EntryRelationship()
        comment_activity.act = {
            "code": {
                "@code": "48767-8",
            },
            "text": {"@xsi:type": "ED", "xmlText": structured_notes_text},
        }
        immunization_entry.entryRelationship.append(comment_activity)

    date_val = readable_date(date_helper(entry.date.isostring)) if entry.date else ""
    vaccine_val = entry.vaccineCode.coding[0].display if (entry.vaccineCode and entry.vaccineCode.coding) else ""
    if misc_notes:
        vaccine_val = f"{vaccine_val}<br />Notes: " + "<br />".join(misc_notes)

    lot_val = entry.lotNumber if entry.lotNumber else ""
    status_val = entry.status if entry.status else ""

    immunization_row = [date_val, vaccine_val, lot_val, status_val]

    return EntryWithRow(
        entry=immunization_entry.model_dump(by_alias=True, exclude_none=True),
        row=immunization_row,
    )
