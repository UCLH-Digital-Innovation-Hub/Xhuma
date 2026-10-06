from typing import Union

from ..helpers import cda_time_bound, code_with_translations, fhir_to_cda_timestamp, readable_date, templateId
from ..models.base import EntryRelationship
from ..models.datatypes import IVL_TS
from .types import EntryWithRow


def observation_entry(entry, index: dict, section_name: Union[str, int]) -> EntryWithRow:
    from ..models.base import Observation

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
        # CRITICAL: Epic's C-CDA parser swallows text preceding a <br/> tag in structured `xmlText` nodes.
        # We must use standard newlines (\n) for the machine-readable `xmlText`, while preserving the
        # HTML <br /> tags exclusively for the narrative table view.
        structured_obs_notes = "\n".join(obs_notes)

        comment_activity = EntryRelationship()
        # TODO: Build an Act with typed code/text rather than assigning a dict;
        # test_observation_entry_xmltext_validation emits a serializer warning for act.
        comment_activity.act = {
            "code": {
                "@code": "48767-8",
            },
            "text": {"@xsi:type": "ED", "xmlText": structured_obs_notes},
        }
        obs.entryRelationship = [comment_activity]

    date_val = "N/A"
    if hasattr(entry, "effectiveDateTime") and entry.effectiveDateTime:
        obs.effectiveTime = IVL_TS(**{"@value": fhir_to_cda_timestamp(entry.effectiveDateTime)})
        date_val = readable_date(fhir_to_cda_timestamp(entry.effectiveDateTime))
    elif hasattr(entry, "effectivePeriod") and entry.effectivePeriod and entry.effectivePeriod.start:
        obs.effectiveTime = IVL_TS(low=cda_time_bound(entry.effectivePeriod.start))
        date_val = readable_date(fhir_to_cda_timestamp(entry.effectivePeriod.start))
    elif hasattr(entry, "effectiveInstant") and entry.effectiveInstant:
        obs.effectiveTime = IVL_TS(**{"@value": fhir_to_cda_timestamp(entry.effectiveInstant)})
        date_val = readable_date(fhir_to_cda_timestamp(entry.effectiveInstant))

    name_val = "N/A"
    if hasattr(entry, "code") and entry.code and entry.code.coding:
        for coding in entry.code.coding:
            if coding.display:
                name_val = coding.display
                break

    if obs_notes and section_name != "Allergies and adverse reactions":
        name_val = f"{name_val}<br />Notes: " + "<br />".join(obs_notes)

    # Build row based on the specific section layout
    if section_name == "Immunisations":
        row = [date_val, name_val, "N/A", "N/A"]
    elif section_name == "Problems":
        row = [date_val, "N/A", name_val]
    elif section_name == "Allergies and adverse reactions":
        # An Observation (e.g. "No known allergy") has an effective date and an
        # observation status, not an AllergyIntolerance assertion/clinical status.
        dates = f"Effective: {date_val}" if date_val != "N/A" else ""
        status = f"Observation status: {entry.status}" if getattr(entry, "status", None) else ""
        row = [dates, name_val, status, "N/A", "N/A", "<br />".join(obs_notes)]
    else:
        # Fallback to old behavior if a simple integer length is passed
        row = [date_val, name_val]
        row_len = (
            int(section_name)
            if isinstance(section_name, int) or (isinstance(section_name, str) and section_name.isdigit())
            else 3
        )
        while len(row) < row_len:
            row.append("N/A")

    return EntryWithRow(entry={"observation": obs.model_dump(by_alias=True, exclude_none=True)}, row=row)
