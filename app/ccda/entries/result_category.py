"""Epic result type classification, independent of report layout and finality."""

import json
import logging
from uuid import NAMESPACE_URL, uuid5

from fhirclient.models.diagnosticreport import DiagnosticReport

from ..models.base import ResultObservation
from ..models.datatypes import CD, CS, II, IVL_TS
from .investigation_grouping import InvestigationGrouping, is_filing_comment

logger = logging.getLogger(__name__)

REPORT_CATEGORY_SYSTEM = "http://hl7.org/fhir/v2/0074"
OBSERVATION_CATEGORY_SYSTEM = "http://hl7.org/fhir/observation-category"
EPIC_CATEGORY_TEMPLATE = "1.2.840.114350.1.72.3.4"
EPIC_CATEGORY_SYSTEM = "1.2.840.114350.1.72.1.5007"
REPORT_CATEGORIES = {"PAT": ("16", "Lab"), "RAD": ("13", "Radiology")}


def result_category(report: DiagnosticReport, grouping: InvestigationGrouping) -> tuple[str, str] | None:
    """Prefer explicit report coding; use linked observations only if absent.

    Unrecognised or conflicting report categories must not be overridden by
    child observations. Text labels are never treated as coded evidence.
    """
    if report.category is not None:
        codes = {c.code for c in report.category.coding or [] if c.system == REPORT_CATEGORY_SYSTEM}
        if len(codes) == 1 and codes <= REPORT_CATEGORIES.keys():
            return REPORT_CATEGORIES[next(iter(codes))]
    else:
        codes = {
            coding.code
            for observation in grouping.observations.values()
            if not is_filing_comment(observation)
            for category in observation.category or []
            for coding in category.coding or []
            if coding.system == OBSERVATION_CATEGORY_SYSTEM
        }
        if codes == {"laboratory"}:
            return "16", "Lab"
    logger.warning("Investigation %s: result category missing, unsupported or conflicting", report.id)
    return None


def category_observation(
    report: DiagnosticReport, grouping: InvestigationGrouping, status: CS, issued: IVL_TS
) -> ResultObservation | None:
    """Emit one category assertion without promoting the source result status."""
    category = result_category(report, grouping)
    if category is None:
        return None
    identifiers = sorted({(i.system, i.value) for i in report.identifier or [] if i.system and i.value})
    return ResultObservation(
        templateId=[
            II(root="2.16.840.1.113883.10.20.22.4.2", extension="2015-08-01"),
            II(root=EPIC_CATEGORY_TEMPLATE),
        ],
        id=[
            II(root=str(uuid5(NAMESPACE_URL, json.dumps(["xhuma-result-category", system, value]))))
            for system, value in identifiers
        ]
        or [II(nullFlavor="UNK")],
        # The supplied Epic specification defines the value vocabulary, but
        # does not provide a coded observation question. Do not invent one.
        code=CD(nullFlavor="OTH", originalText="Result type"),
        statusCode=status,
        effectiveTime=issued,
        value=CD(code=category[0], displayName=category[1], codeSystem=EPIC_CATEGORY_SYSTEM),
    )
