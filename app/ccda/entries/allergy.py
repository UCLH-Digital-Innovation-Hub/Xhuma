from fhirclient.models import allergyintolerance

from ..helpers import code_with_translations, readable_date
from ..models.allergy import (
    Allergy,
    AllergyComment,
    AllergyCommentAct,
    AllergyEffectiveTime,
    AllergyEntry,
    AllergyIntoleranceObservation,
    AllergyObservation,
    AllergyReaction,
    AllergySubstanceCode,
    Participant2,
    ParticipantRole,
    PlayingEntity,
    ReactionObservation,
    ReactionSeverity,
    SeverityObservation,
)
from ..models.datatypes import CD, ED, II, IVL_TS, IVXB_TS
from .allergy_metadata import cda_time, date_value, source_author, source_informant
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

# Explicit absence codes map to a positive allergy type with observation negation.
# https://hl7.org/fhir/us/ccda/en/ConceptMap-FC-NoKnownAllergies.html
_NO_KNOWN_ALLERGIES = {
    "716186003": ("419199007", "Allergy to substance"),
    "409137002": ("416098002", "Allergy to drug"),
    "429625007": ("414285001", "Allergy to food"),
}


def _no_known_allergy_type(entry: allergyintolerance.AllergyIntolerance) -> CD | None:
    """Map an explicit SNOMED absence code to the CDA type that must be negated.

    Match system and code, never display text or an empty list. The coded scope
    takes precedence over generic type/category fields, so absence of drug or
    food allergy does not become absence of all allergies.
    """
    for coding in entry.code.coding or []:
        if coding.system == "http://snomed.info/sct" and coding.code in _NO_KNOWN_ALLERGIES:
            code, display = _NO_KNOWN_ALLERGIES[coding.code]
            return CD(code=code, displayName=display, codeSystem="2.16.840.1.113883.6.96", codeSystemName="SNOMED CT")
    return None


def _date_low(date) -> IVXB_TS:
    """Build an interval's low timestamp, preserving precision or marking it UNK."""
    return cda_time(date)


def allergy_onset_sort_key(entry):
    """Return an ascending onset-date key with unknown onsets sorted last.

    Use onsetDateTime or onsetPeriod.start, ignoring time of day. Retain partial
    date precision and never substitute the assertion date. A stable sort keeps
    source order for ties, including records without a sortable onset date.
    """
    onset = getattr(entry, "onsetDateTime", None)
    period = getattr(entry, "onsetPeriod", None)
    if onset is None and period is not None:
        onset = period.start
    value = date_value(onset)
    # Oldest onset date first, with partial dates ordered by their supplied precision.
    # Undated/age-only/text-only onsets (and non-allergy Observations) come last.
    # Python's stable sort preserves source order for equal dates and unknowns;
    # asserted dates must never be used as a substitute for missing onset dates.
    return (value is None, value[:10] if value else "")


def _display_date(date):
    """Format a full date as DD/MM/YYYY, retain partial dates, or return blank.

    Time components are omitted from the narrative date; structured timestamps
    are handled separately by cda_time and retain the supplied time and offset.
    """
    value = date_value(date)
    if not value:
        return ""
    day = value.split("T")[0]
    return readable_date(day.replace("-", "")) if len(day) == 10 else day


def _lines(values):
    """Build the narrative table's line-break cell, or an empty string for no lines.

    This markup is for display only; structured note text uses plain newlines.
    """
    return {"BR": [f"{value} <br />" for value in values]} if values else ""


def _dates(entry):
    """Build labelled narrative lines for the allergy's supplied dates and onset.

    Keep onset, assertion, last occurrence and each reaction onset separate.
    Onset may be a date, period, free text, age or age range. Period bounds describe
    when onset occurred, not when the allergy resolved. Omit absent values.
    """
    lines = []
    if entry.onsetDateTime:
        lines.append(f"Onset: {_display_date(entry.onsetDateTime)}")
    elif entry.onsetPeriod:
        if entry.onsetPeriod.start:
            lines.append(f"Onset from: {_display_date(entry.onsetPeriod.start)}")
        if entry.onsetPeriod.end:
            # This bounds the onset period, not the end of the allergy.
            lines.append(f"Onset to: {_display_date(entry.onsetPeriod.end)}")
    elif entry.onsetString:
        lines.append(f"Onset: {entry.onsetString}")
    elif entry.onsetAge:
        lines.append(f"Onset age: {_quantity(entry.onsetAge)}")
    elif entry.onsetRange:
        low, high = entry.onsetRange.low, entry.onsetRange.high
        if low:
            lines.append(f"Onset age from: {_quantity(low)}")
        if high:
            lines.append(f"Onset age to: {_quantity(high)}")
    if entry.assertedDate:
        lines.append(f"Asserted: {_display_date(entry.assertedDate)}")
    if entry.lastOccurrence:
        lines.append(f"Last occurrence: {_display_date(entry.lastOccurrence)}")
    for number, reaction in enumerate(entry.reaction or [], 1):
        if reaction.onset:
            lines.append(f"Reaction {number} onset: {_display_date(reaction.onset)}")
    return _lines(lines)


def _quantity(quantity):
    """Render an onset age using its comparator, value and unit (or unit code)."""
    return " ".join(
        str(v) for v in (quantity.comparator, quantity.value, quantity.unit or quantity.code) if v is not None
    )


def _allergy_type(entry: allergyintolerance.AllergyIntolerance) -> CD:
    """Map the FHIR type and single category to a SNOMED allergy/intolerance type.

    Use a general allergy or adverse-reaction concept when the combination is
    unmapped or multiple categories prevent choosing a single specific type.
    """
    categories = set(entry.category or [])
    category = next(iter(categories)) if len(categories) == 1 else None
    code, display = _ALLERGY_TYPES.get(
        (entry.type, category),
        ("419199007", "Allergy to substance")
        if entry.type == "allergy"
        else ("420134006", "Propensity to adverse reaction"),
    )
    return CD(code=code, displayName=display, codeSystem="2.16.840.1.113883.6.96", codeSystemName="SNOMED CT")


def allergy(entry: allergyintolerance.AllergyIntolerance, index: dict | None = None) -> EntryWithRow:
    """Convert a coded FHIR allergy into a structured CDA entry and narrative row.

    Use the optional bundle index to resolve asserter, recorder and note authors.
    The row contains dates, description, statuses, reactions, severity and notes;
    structured content carries native CDA mappings without copying unmapped FHIR
    extensions. Ordering across allergies is handled by the section converter.
    """
    index = index or {}
    # Preserve the source resource identity; model defaults cover resources without an ID.
    identifiers = [II(root=entry.id)] if entry.id else []
    identifiers.extend(II(root=i.system, extension=i.value) for i in entry.identifier or [] if i.value)
    source_id = {"id": identifiers} if identifiers else {}
    substance = code_with_translations(list(entry.code.coding))
    # playingEntity.code is CE; keep the helper's translations but use its declared datatype.
    substance_ce = AllergySubstanceCode.model_validate(
        substance.model_dump(exclude={"resource_type"}, exclude_none=True)
    )
    absence_type = _no_known_allergy_type(entry)
    # A no-known-allergy finding is not an allergen. Keep its source wording in
    # the narrative, but mark the required CDA substance participant not applicable.
    participant_code = AllergySubstanceCode(nullFlavor="NA") if absence_type is not None else substance_ce
    notes = [note.text for note in entry.note or [] if note.text]
    degraded_text = None
    if entry.code.text and any(
        coding.code == "196461000000101" and coding.system == "http://snomed.info/sct" for coding in entry.code.coding
    ):
        degraded_text = f"Transfer degraded allergy text: {entry.code.text}"
        notes.append(degraded_text)
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
    for note in entry.note or []:
        # Preserve each annotation and its author/time separately, including repeats.
        # Match medicines: plain newlines in structured notes, breaks only in the table.
        author = source_author(note.authorReference, note.time, index, entry, name=note.authorString)
        relationships.append(
            AllergyComment(
                act=AllergyCommentAct(
                    code=CD(code="48767-8"),
                    text=ED(xmlText=note.text),
                    effectiveTime=IVL_TS(low=_date_low(note.time)) if note.time else None,
                    author=[author] if author else None,
                )
            )
        )
    if degraded_text:
        relationships.append(
            AllergyComment(act=AllergyCommentAct(code=CD(code="48767-8"), text=ED(xmlText=degraded_text)))
        )
    informant, asserter_name = source_informant(entry.asserter, index, entry)
    # assertedDate is the assertion date, not necessarily the recorder's authoring time.
    recorder = source_author(entry.recorder, None, index, entry)
    if asserter_name:
        notes.append(f"Asserter: {asserter_name}")
    observation = AllergyIntoleranceObservation(
        **source_id,
        effectiveTime=clinical_time,
        negationInd=True if absence_type is not None else None,
        value=absence_type if absence_type is not None else _allergy_type(entry),
        author=[recorder] if recorder else None,
        informant=[informant] if informant else None,
        participant=[Participant2(participantRole=ParticipantRole(playingEntity=PlayingEntity(code=participant_code)))],
        entryRelationship=relationships or None,
    )
    act = Allergy(
        **source_id,
        effectiveTime=IVL_TS(low=asserted_low),
        entryRelationship=[AllergyObservation(observation=observation)],
    )
    statuses = [
        f"{label}: {value.replace('-', ' ').capitalize()}"
        for label, value in (
            ("Clinical status", entry.clinicalStatus),
            ("Verification status", entry.verificationStatus),
            ("Criticality", entry.criticality),
        )
        if value
    ]
    return EntryWithRow(
        entry=AllergyEntry(act=act).model_dump(by_alias=True, exclude_none=True),
        row=[
            _dates(entry),
            substance_ce.displayName or entry.code.text or "",
            _lines(statuses),
            "; ".join(dict.fromkeys(reaction_labels)),
            "; ".join(dict.fromkeys(severity_labels)),
            _lines(notes),
        ],
    )
