from ..helpers import code_with_translations, organization_to_author
from ..models.base import ResultObservation, ResultsOrganizer
from ..models.datatypes import PQ


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
