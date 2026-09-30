"""Map FHIR STU3 result values and their narrative together, without interpreting prose."""

import json
import logging
import re
from dataclasses import dataclass

from fhirclient.models.codeableconcept import CodeableConcept
from fhirclient.models.observation import Observation
from fhirclient.models.quantity import Quantity

from ..models.datatypes import BL, CD, CODE_SYSTEM_NAMES, INT, IVL_PQ, IVXB_PQ, PQ, PQR, RTO_PQ_PQ, ST

logger = logging.getLogger(__name__)
UCUM = "http://unitsofmeasure.org"
# Only accept known spellings when the sender has not identified UCUM explicitly.
# Other units remain in a PQ translation, rather than claiming an unverified unit.
KNOWN_UCUM = {
    "1",
    "%",
    "g",
    "mg",
    "ug",
    "kg",
    "L",
    "mL",
    "g/L",
    "g/dL",
    "mg/L",
    "mg/dL",
    "ug/L",
    "mmol/L",
    "umol/L",
    "mol/L",
    "nmol/L",
    "pmol/L",
    "mm[Hg]",
    "fL",
    "pg",
    "s",
    "min",
    "10*9/L",
    "10*12/L",
    "[IU]/L",
    "m[IU]/L",
}


@dataclass(frozen=True)
class ResultValue:
    """A CDA value and matching display; None means no separate value was supplied."""

    value: ST | BL | INT | CD | PQ | IVL_PQ | RTO_PQ_PQ | None
    text: str | None


def coding_oid(system: str | None) -> str | None:
    """Resolve known terminology URIs or an explicit OID without inventing one."""
    if system in CODE_SYSTEM_NAMES:
        return CODE_SYSTEM_NAMES[system]
    if system and system.startswith("urn:oid:") and re.fullmatch(r"[0-2](?:\.[0-9]+)+", system[8:]):
        return system[8:]
    return None


def concept_text(concept: CodeableConcept) -> str:
    """Use original concept text, otherwise retain each supplied display/code."""
    return concept.text or "; ".join(c.display or c.code or "" for c in concept.coding or [])


def coded_value(concept: CodeableConcept) -> ResultValue:
    """Map recognised codings to CD/translation, retaining unmappable source coding as text."""
    codings = sorted(concept.coding or [], key=lambda c: c.system != "http://snomed.info/sct")
    mapped, unmapped = [], []
    for coding in codings:
        oid = coding_oid(coding.system)
        if coding.code and oid:
            mapped.append(CD(code=coding.code, codeSystem=oid, displayName=coding.display))
        else:
            unmapped.append(" | ".join(filter(None, (coding.system, coding.code, coding.display))))
    value = mapped[0] if mapped else CD(nullFlavor="OTH")
    original = concept.text or (concept_text(concept) if not mapped else None)
    if original:
        value.originalText = original
    translations = mapped[1:]
    translations.extend(CD(nullFlavor="OTH", originalText=text) for text in unmapped if text)
    if translations:
        value.translation = translations
    return ResultValue(value, concept_text(concept) or "Unspecified coded result")


def quantity_text(quantity: Quantity) -> str:
    """Show the source number and unit without substituting a machine unit label."""
    return " ".join(
        str(x)
        for x in (
            quantity.comparator,
            quantity.value if quantity.value is not None else "Value not supplied",
            quantity.unit or quantity.code,
        )
        if x is not None and x != ""
    )


def physical_quantity(quantity: Quantity, *, number: float | None = None) -> PQ:
    """Build PQ fields, preserving non-UCUM units in a translation.

    A declared UCUM code is trusted. For an undeclared unit only a small known
    set is accepted; this function is not a general UCUM validator/converter.
    """
    amount = quantity.value if number is None else number
    if amount is None:
        return PQ(nullFlavor="UNK")
    unit = None
    if quantity.system == UCUM and quantity.code:
        unit = quantity.code
    elif not quantity.system and quantity.unit in KNOWN_UCUM:
        unit = quantity.unit
    elif quantity.system == UCUM and not quantity.code and quantity.unit in KNOWN_UCUM:
        unit = quantity.unit
    if unit:
        return PQ(value=amount, unit=unit)
    # Missing units are not assumed to mean dimensionless. Retain the supplied
    # magnitude and unit/coding in a translation instead of assigning unit 1.
    translation = PQR(value=amount)
    oid = coding_oid(quantity.system)
    if oid and quantity.code:
        translation.code = quantity.code
        translation.codeSystem = oid
    else:
        translation.nullFlavor = "OTH"
    source_unit = " | ".join(filter(None, (quantity.unit, quantity.system, quantity.code)))
    translation.originalText = source_unit or "Unit not supplied"
    return PQ(nullFlavor="OTH", translation=[translation])


def quantity_boundary(quantity: Quantity, *, inclusive: bool, number: float | None = None) -> IVXB_PQ:
    """Reuse the quantity mapping for a typed interval boundary."""
    mapped = physical_quantity(quantity, number=number)
    return IVXB_PQ(
        value=mapped.value,
        unit=mapped.unit,
        nullFlavor=mapped.nullFlavor,
        translation=mapped.translation,
        inclusive=inclusive,
    )


def unmapped_value(observation: Observation, field: str, supplied) -> ResultValue:
    """Retain unsupported source content as explicit text, and log its type for review."""
    logger.warning("Observation %s: unmapped result value type/content %s", observation.id, field)
    raw = supplied.as_json() if hasattr(supplied, "as_json") else supplied
    text = f"Unmapped {field}: {json.dumps(raw, ensure_ascii=False)}"
    return ResultValue(ST(text=text), text)


def map_result_value(observation: Observation) -> ResultValue:
    """Convert supported FHIR values, keeping zero/false and explicit absence reasons.

    Unsupported types are preserved as labelled ST source text rather than
    decoded, numerically interpreted, or silently dropped.
    """
    values = [
        (json_name, getattr(observation, name))
        for name, json_name, *_ in observation.elementProperties()
        if json_name.startswith("value") and getattr(observation, name) is not None
    ]
    if not values:
        reason = observation.dataAbsentReason
        if not reason:
            return ResultValue(None, None)  # A comment-only result need not invent a value.
        text = concept_text(reason) or "Reason not specified"
        codes = [
            c.code
            for c in reason.coding or []
            if c.system
            in ("http://hl7.org/fhir/data-absent-reason", "http://terminology.hl7.org/CodeSystem/data-absent-reason")
        ]
        null_flavor = {
            "unknown": "UNK",
            "asked": "ASKU",
            "asked-unknown": "ASKU",
            "temp": "NAV",
            "temp-unknown": "NAV",
            "not-asked": "NASK",
            "masked": "MSK",
            "not-applicable": "NA",
            "unsupported": "OTH",
            "as-text": "OTH",
            "error": "INV",
            "not-performed": "NA",
        }.get(codes[0] if codes else None, "UNK")
        return ResultValue(ST(nullFlavor=null_flavor), f"No value supplied: {text}")
    if len(values) != 1:
        return unmapped_value(
            observation,
            "multiple value fields",
            {name: value.as_json() if hasattr(value, "as_json") else value for name, value in values},
        )
    field, supplied = values[0]
    if field == "valueString":
        if supplied == "":
            return ResultValue(ST(nullFlavor="UNK"), "Empty string supplied")
        return ResultValue(ST(text=supplied), supplied)
    if field == "valueBoolean":
        return ResultValue(BL(value=supplied), str(supplied))
    if field == "valueInteger":
        return ResultValue(INT(value=supplied), str(supplied))
    if field == "valueCodeableConcept":
        return coded_value(supplied)
    if field == "valueQuantity":
        if supplied.value is None:
            return ResultValue(
                PQ(nullFlavor="UNK"),
                f"Quantity not supplied{': ' + quantity_text(supplied) if quantity_text(supplied) else ''}",
            )
        if not supplied.comparator:
            return ResultValue(physical_quantity(supplied), quantity_text(supplied))
        if supplied.comparator not in ("<", "<=", ">", ">="):
            return unmapped_value(observation, field, supplied)
        if supplied.comparator in ("<", "<=") and (
            supplied.value < 0 or (supplied.value == 0 and supplied.comparator == "<")
        ):
            # The agreed zero-bound convention cannot express this interval.
            # Preserve the source instead of producing inverted/empty bounds.
            return unmapped_value(observation, field, supplied)
        boundary = quantity_boundary(supplied, inclusive="=" in supplied.comparator)
        # Retain the agreed zero-bound convention from HL7 quantity mapping.
        if supplied.comparator.startswith("<"):
            value = IVL_PQ(low=quantity_boundary(supplied, number=0, inclusive=True), high=boundary)
        else:
            value = IVL_PQ(low=boundary, high=IVXB_PQ(nullFlavor="PINF"))
        return ResultValue(value, quantity_text(supplied))
    if field == "valueRange":
        if not supplied.low and not supplied.high:
            return ResultValue(IVL_PQ(nullFlavor="UNK"), "Range bounds not supplied")
        value = IVL_PQ()
        for name in ("low", "high"):
            bound = getattr(supplied, name)
            if bound:
                if bound.comparator:
                    return unmapped_value(observation, field, supplied)
                setattr(value, name, quantity_boundary(bound, inclusive=True))
        low = quantity_text(supplied.low) if supplied.low else None
        high = quantity_text(supplied.high) if supplied.high else None
        text = f"{low} – {high}" if low and high else f">= {low}" if low else f"<= {high}"
        return ResultValue(value, text)
    if field == "valueRatio":
        if (
            not supplied.numerator
            or not supplied.denominator
            or supplied.denominator.value == 0
            or supplied.numerator.comparator
            or supplied.denominator.comparator
        ):
            return unmapped_value(observation, field, supplied)
        return ResultValue(
            RTO_PQ_PQ(
                numerator=physical_quantity(supplied.numerator),
                denominator=physical_quantity(supplied.denominator),
            ),
            f"{quantity_text(supplied.numerator)} / {quantity_text(supplied.denominator)}",
        )
    return unmapped_value(observation, field, supplied)
