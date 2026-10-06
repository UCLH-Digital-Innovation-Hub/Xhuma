"""Reference ranges with source units, qualifiers and safe numeric comparison."""

from fhirclient.models.observation import Observation, ObservationReferenceRange
from fhirclient.models.quantity import Quantity

from ..models.base import ObservationRange, ReferenceRange
from ..models.datatypes import IVL_PQ, ST
from .result_values import concept_text, physical_quantity, quantity_boundary, quantity_text


def reference_range(reference: ObservationReferenceRange) -> tuple[ReferenceRange, str]:
    """Keep one source range in one wrapper, including text and population labels.

    Bounds use their own units; absent units are never borrowed from the result.
    Text-only ranges use ST. Missing bounds are omitted rather than invented.
    """
    labels = [reference.text] if reference.text else []
    if reference.type:
        labels.append(f"Range type: {concept_text(reference.type)}")
    labels.extend(f"Applies to: {concept_text(c)}" for c in reference.appliesTo or [])
    if reference.age:
        age = reference.age
        labels.append("Age: " + " – ".join(quantity_text(q) for q in (age.low, age.high) if q is not None))
    low, high = reference.low, reference.high
    if low is not None and high is not None:
        bounds = f"{quantity_text(low)} – {quantity_text(high)}"
    elif low is not None:
        bounds = f">= {quantity_text(low)}"
    elif high is not None:
        bounds = f"<= {quantity_text(high)}"
    else:
        bounds = None
    narrative = "\n".join([*labels, *([bounds] if bounds else [])])
    if low is not None or high is not None:
        value = IVL_PQ(
            low=quantity_boundary(low, inclusive=True) if low is not None else None,
            high=quantity_boundary(high, inclusive=True) if high is not None else None,
        )
    else:
        value = ST(text=narrative) if narrative else ST(nullFlavor="UNK")
    return ReferenceRange(observationRange=ObservationRange(text=narrative or None, value=value)), narrative


def comparable_quantities(result: Quantity, boundary: Quantity) -> bool:
    """Compare only known identical UCUM units; never infer or convert units."""
    left, right = physical_quantity(result), physical_quantity(boundary)
    return left.unit is not None and left.unit == right.unit and left.value is not None and right.value is not None


def outside_reference_range(observation: Observation) -> bool:
    """Highlight only an unqualified numeric range whose units match the result.

    Population/age/type-qualified ranges need clinical selection. Multiple ranges
    are left unclassified rather than highlighting against an arbitrary interval.
    The supplied interpretationCode is preserved separately by the converter.
    """
    result = observation.valueQuantity
    ranges = observation.referenceRange or []
    if result is None or result.value is None or result.comparator or len(ranges) != 1:
        return False
    reference = ranges[0]
    if reference.appliesTo or reference.age or reference.type:
        return False
    return any(
        bound is not None
        and not bound.comparator
        and comparable_quantities(result, bound)
        and (result.value < bound.value if side == "low" else result.value > bound.value)
        for side, bound in (("low", reference.low), ("high", reference.high))
    )
