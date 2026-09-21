from fhirclient.models import allergyintolerance

from ..helpers import code_with_translations, readable_date
from ..models.allergy import (
    Allergy,
    AllergyComment,
    AllergyEffectiveTime,
    AllergyEntry,
    AllergyIntoleranceObservation,
    AllergyObservation,
    AllergyReaction,
    Participant2,
    ParticipantRole,
    PlayingEntity,
    ReactionObservation,
    ReactionSeverity,
    SeverityObservation,
)
from ..models.base import Act
from ..models.datatypes import CD, CE, ED, II, IVL_TS, IVXB_TS
from .types import EntryWithRow

# SNOMED concepts from the HL7 C-CDA Allergy and Intolerance Type mappings.
# https://hl7.org/fhir/us/ccda/en/ConceptMap-CF-AllergyIntoleranceType.html
_ALLERGY_TYPES = {
    ("allergy", "medication"): ("416098002", "Allergy to drug"),
    ("allergy", "food"): ("414285001", "Allergy to food"),
    ("intolerance", "medication"): ("59037007", "Intolerance to drug"),
    ("intolerance", "food"): ("235719002", "Intolerance to food"),
    (None, "medication"): ("419511003", "Propensity to adverse reactions to drug"),
    (None, "food"): ("418471000", "Propensity to adverse reactions to food"),
}

# SNOMED severity qualifiers corresponding to FHIR reaction.severity.
_SEVERITIES = {"mild": ("255604002", "Mild"), "moderate": ("6736007", "Moderate"), "severe": ("24484000", "Severe")}


def _date_low(date) -> IVXB_TS:
    if date is None or date.as_json() is None:
        return IVXB_TS(nullFlavor="UNK")
    # Preserve the supplied date precision, including year-only FHIR dates.
    return IVXB_TS(value=date.as_json().split("T")[0].replace("-", ""))


def _allergy_type(entry: allergyintolerance.AllergyIntolerance) -> CD:
    categories = set(entry.category or [])
    category = next(iter(categories)) if len(categories) == 1 else None
    code, display = _ALLERGY_TYPES.get(
        (entry.type, category),
        ("419199007", "Allergy to substance")
        if entry.type == "allergy"
        else ("420134006", "Propensity to adverse reaction"),
    )
    return CD(code=code, displayName=display, codeSystem="2.16.840.1.113883.6.96", codeSystemName="SNOMED CT")


def allergy(entry: allergyintolerance.AllergyIntolerance) -> EntryWithRow:
    # Preserve the source resource identity; model defaults cover resources without an ID.
    source_id = {"id": [II(root=entry.id)]} if entry.id else {}
    substance = code_with_translations(list(entry.code.coding))
    # playingEntity.code is CE; keep the helper's translations but use its declared datatype.
    substance_ce = CE.model_validate(substance.model_dump(exclude={"resource_type"}, exclude_none=True))
    notes = [note.text for note in entry.note or [] if note.text]
    if entry.code.text and any(
        coding.code == "196461000000101" and coding.system == "http://snomed.info/sct" for coding in entry.code.coding
    ):
        notes.append(f"Transfer degraded allergy text: {entry.code.text}")
    notes = list(dict.fromkeys(notes))
    asserted_low = _date_low(entry.assertedDate)
    onset = entry.onsetDateTime
    if onset is None and entry.onsetPeriod is not None:
        onset = entry.onsetPeriod.start
    clinical_time = AllergyEffectiveTime(
        low=_date_low(onset),
        # FHIR onsetPeriod.end bounds onset; it is not a resolution date.
        high=IVXB_TS(nullFlavor="UNK") if entry.clinicalStatus == "resolved" else None,
    )

    reactions = []
    reaction_labels = []
    severity_labels = []
    for reaction in entry.reaction or []:
        for manifestation in reaction.manifestation or []:
            manifestation_code = code_with_translations(list(manifestation.coding))
            label = manifestation.text or manifestation_code.displayName or manifestation_code.code
            reaction_labels.append(label)
            severity = None
            if reaction.severity:
                severity_code, severity_display = _SEVERITIES[reaction.severity]
                # severity_labels.append(f"{label}: {severity_display}")
                severity_labels.append(f"{severity_display}")
                severity = ReactionSeverity(
                    observation=SeverityObservation(
                        value=CD(
                            code=severity_code,
                            displayName=severity_display,
                            codeSystem="2.16.840.1.113883.6.96",
                            codeSystemName="SNOMED CT",
                        )
                    )
                )
            reactions.append(
                AllergyReaction(
                    observation=ReactionObservation(
                        effectiveTime=IVL_TS(low=_date_low(reaction.onset)),
                        value=manifestation_code,
                        entryRelationship=[severity] if severity else None,
                    )
                )
            )

    relationships = list(reactions)
    if notes:
        # Match medicines: plain newlines in structured notes, breaks only in the table.
        relationships.append(AllergyComment(act=Act(code=CD(code="48767-8"), text=ED(xmlText="\n".join(notes)))))
    observation = AllergyIntoleranceObservation(
        **source_id,
        effectiveTime=clinical_time,
        value=_allergy_type(entry),
        participant=[Participant2(participantRole=ParticipantRole(playingEntity=PlayingEntity(code=substance_ce)))],
        entryRelationship=relationships or None,
    )
    act = Allergy(
        **source_id,
        effectiveTime=IVL_TS(low=asserted_low),
        entryRelationship=[AllergyObservation(observation=observation)],
    )
    row_date = ""
    if asserted_low.value:
        row_date = readable_date(asserted_low.value) if len(asserted_low.value) == 8 else entry.assertedDate.as_json()
    return EntryWithRow(
        entry=AllergyEntry(act=act).model_dump(by_alias=True, exclude_none=True),
        row=[
            row_date,
            substance_ce.displayName or entry.code.text or "",
            "; ".join(dict.fromkeys(reaction_labels)),
            "; ".join(dict.fromkeys(severity_labels)),
            {"BR": [f"{note} <br />" for note in notes]} if notes else "",
        ],
    )
