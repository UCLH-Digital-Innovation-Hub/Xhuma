"""Native CDA dates and provenance for GP Connect allergies."""

from ..helpers import cda_timestamp
from ..models.admin import Person
from ..models.allergy import (
    AllergyAssignedAuthor,
    AllergyAssignedEntity,
    AllergyAuthor,
    AllergyIdentifier,
    AllergyInformant,
    AllergyRelatedEntity,
)


def reference_details(reference, index, owner):
    """Return a display label and CDA identifiers for a FHIR person reference.

    Used for asserters, recorders and note authors. ``index`` contains resources
    from the current bundle; ``owner`` is the allergy that may contain an inline
    resource. Resolution is local only: this function never fetches a resource.
    The returned label may be a reference/identifier fallback rather than a name;
    callers use _person() to avoid treating those fallbacks as actual person names.
    """
    if reference is None:
        return None, []

    # Usually the reference is a bundle key such as "Practitioner/123".
    ref = reference.reference
    resource = index.get(ref)
    if resource is None and ref and ref.startswith("#"):
        # "#123" points inside the owning allergy, not to a separate bundle entry.
        resource = next((r for r in owner.contained or [] if r.id == ref[1:]), None)
    if resource is None and ref:
        # GP Connect may use absolute references while the bundle index uses Type/id.
        resource = index.get("/".join(ref.rstrip("/").split("/")[-2:]))

    # Accept a plain name or a list of FHIR HumanName values. Prefer the usual name,
    # then the first supplied name; preserve its text before assembling name parts.
    names = getattr(resource, "name", None) or []
    name = names if isinstance(names, str) else None
    if names and not name:
        selected = next((n for n in names if n.use == "usual"), names[0])
        name = (
            selected.text
            or " ".join(
                [*(selected.prefix or []), *(selected.given or []), selected.family or "", *(selected.suffix or [])]
            ).strip()
        )

    # Map supplied identifiers directly: the system (including a URL) becomes the
    # CDA root and the value becomes the extension. Do not generate replacement IDs.
    identifiers = [
        AllergyIdentifier(root=i.system, extension=i.value)
        for i in getattr(resource, "identifier", None) or []
        if i.value
    ]
    if reference.identifier and reference.identifier.value:
        # A reference can carry an identifier even if its resource cannot be resolved.
        identifiers.append(AllergyIdentifier(root=reference.identifier.system, extension=reference.identifier.value))
    if ref and not identifiers:
        # Keep the original reference as the identity when no identifier was supplied.
        identifiers.append(AllergyIdentifier(root=ref))

    # Best available label: resolved name, supplied display, reference, identifier.
    # This still gives the Notes column useful context when the person is unresolved.
    return name or reference.display or ref or (
        reference.identifier.value if reference.identifier else None
    ), identifiers


def source_author(reference, date, index, owner, name=None):
    """Build a CDA author for a recorder or note author using local bundle data.

    ``name`` is a fallback for a note's authorString. Return None if no identity
    or label is available; otherwise mark a missing identifier or time as unknown.
    ``date`` must be the supplied authoring time, not an inferred assertion date.
    """
    label, identifiers = reference_details(reference, index, owner)
    label = label or name
    if not identifiers and not label:
        return None
    return AllergyAuthor(
        time=cda_timestamp(date),
        assignedAuthor=AllergyAssignedAuthor(
            id=identifiers or [AllergyIdentifier(nullFlavor="UNK")],
            assignedPerson=_person(label, reference),
        ),
    )


def source_informant(reference, index, owner):
    """Return the allergy asserter's CDA informant and narrative display label.

    Resolve the reference locally through reference_details. Use assignedEntity
    when identifiers are available, or relatedEntity for a label-only source.
    Return (None, None) when there is no source identity or label.
    """
    label, identifiers = reference_details(reference, index, owner)
    if identifiers:
        return AllergyInformant(
            assignedEntity=AllergyAssignedEntity(id=identifiers, assignedPerson=_person(label, reference))
        ), label
    if label:
        return AllergyInformant(relatedEntity=AllergyRelatedEntity(relatedPerson=Person(name=label))), label
    return None, None


def _person(label, reference):
    """Return a CDA Person for a name, excluding reference/identifier fallbacks.

    A fallback label is useful in the narrative but must not become a person's
    name in structured CDA. Return None for such labels or an absent label.
    """
    identifier = getattr(reference, "identifier", None)
    fallbacks = (getattr(reference, "reference", None), getattr(identifier, "value", None))
    return Person(name=label) if label and label not in fallbacks else None
