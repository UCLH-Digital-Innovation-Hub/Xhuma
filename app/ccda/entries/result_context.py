"""Typed specimen mapping and Epic-specific investigation timestamps."""

from fhirclient.models.diagnosticreport import DiagnosticReport
from fhirclient.models.specimen import Specimen as FHIRSpecimen

from ..helpers import cda_time_bound, cda_time_interval
from ..models.base import ResultOrganizerTime
from ..models.datatypes import CE, II, IVL_TS
from ..models.specimen import Specimen, SpecimenPlayingEntity, SpecimenRole
from .result_values import coded_value


def report_issue_time(report: DiagnosticReport) -> IVL_TS:
    """Use the available report-issued instant for all Epic result components.

    Epic calls this the finalising instant. issued is our source proxy, not
    evidence of final status and not the GP filing/receipt/collection time.
    A missing issued value stays unknown; do not fall back to an analyte date.
    """
    return cda_time_interval(report.issued, report.issued)


def report_specimens(report: DiagnosticReport, index: dict) -> list[FHIRSpecimen]:
    """Resolve report specimens once, deduplicating aliases of the same object."""
    found = {}
    for reference in report.specimen or []:
        specimen = index.get(reference.reference)
        if isinstance(specimen, FHIRSpecimen):
            found.setdefault(id(specimen), specimen)
    return list(found.values())


def specimen_collection_time(specimen: FHIRSpecimen) -> ResultOrganizerTime:
    """Map a collection instant/period, retaining missing period endpoints."""
    collection = specimen.collection
    if collection and collection.collectedDateTime:
        return ResultOrganizerTime(
            low=cda_time_bound(collection.collectedDateTime), high=cda_time_bound(collection.collectedDateTime)
        )
    if collection and collection.collectedPeriod:
        return ResultOrganizerTime(
            low=cda_time_bound(collection.collectedPeriod.start), high=cda_time_bound(collection.collectedPeriod.end)
        )
    return ResultOrganizerTime(low=cda_time_bound(None), high=cda_time_bound(None))


def organizer_collection_time(report: DiagnosticReport, index: dict) -> ResultOrganizerTime:
    """Use a shared collection interval only when all report specimens agree.

    Different or missing collection times cannot identify one Epic collection
    instant. Keep the organizer unknown and retain individual dates in narrative.
    Never select one sample arbitrarily or use report.issued as collection time.
    """
    specimens = report_specimens(report, index)
    intervals = [specimen_collection_time(s) for s in specimens]
    unresolved = any(not isinstance(index.get(r.reference), FHIRSpecimen) for r in report.specimen or [])
    if intervals and not unresolved and all(interval == intervals[0] for interval in intervals):
        return intervals[0]
    return ResultOrganizerTime(low=cda_time_bound(None), high=cda_time_bound(None))


def cda_specimen(specimen: FHIRSpecimen) -> Specimen:
    """Keep sample identifiers and source specimen type in the CDA participant."""
    mapped = coded_value(specimen.type).value if specimen.type else CE(nullFlavor="UNK")
    code = CE(
        code=mapped.code,
        codeSystem=mapped.codeSystem,
        displayName=mapped.displayName,
        originalText=mapped.originalText,
        translation=mapped.translation,
        nullFlavor=mapped.nullFlavor,
    )
    identifiers = list(specimen.identifier or [])
    if specimen.accessionIdentifier:
        identifiers.append(specimen.accessionIdentifier)
    unique = {(i.system, i.value): i for i in identifiers}
    return Specimen(
        specimenRole=SpecimenRole(
            id=[II(root=i.system, extension=i.value) for i in unique.values()] or None,
            specimenPlayingEntity=SpecimenPlayingEntity(code=code),
        )
    )
