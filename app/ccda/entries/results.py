import logging
from dataclasses import dataclass

from fhirclient.models import diagnosticreport as dr
from fhirclient.models import observation as obs
from fhirclient.models.specimen import Specimen

from ..helpers import (
    cda_time_interval,
    code_with_translations,
    id_helper,
)
from ..models.base import ResultObservation, ResultsOrganizer
from ..models.datatypes import CD, CS, II, IVL_TS
from .investigation_grouping import group_investigation, has_result_value, is_filing_comment, observation_label
from .result_context import cda_specimen, organizer_collection_time, report_issue_time, report_specimens
from .result_values import map_result_value
from .types import EntryWithRow

logger = logging.getLogger(__name__)

COMMENT_NOTE_SNOMED = ["37331000000100", "364712009"]  # SNOMED codes for comment note
INVESTIGATION_RESULT = "24641000000107"
TRANSFER_DEGRADED = "196411000000103"


@dataclass(frozen=True)
class ResultTableRow:
    cells: list[str]


@dataclass(frozen=True)
class ResultTable:
    title: str
    headers: list[str]
    rows: list[ResultTableRow]


@dataclass(frozen=True)
class ResultWithRow(EntryWithRow):
    entry: ResultObservation
    row: ResultTableRow


@dataclass(frozen=True)
class InvestigationWithTable:
    organizer: dict
    table: dict


def is_comment_note(observation: obs.Observation) -> bool:
    if observation.code and observation.code.coding:
        for coding in observation.code.coding:
            if coding.code in COMMENT_NOTE_SNOMED:
                return True
    return False


def is_test_group_header(observation: obs.Observation) -> bool:
    """Legacy caption/category heuristic; never use this to select or drop results."""

    if is_comment_note(observation):
        return False
    if observation.valueQuantity:
        return False
    if observation.code and observation.code.coding:
        for coding in observation.code.coding:
            if coding.code == INVESTIGATION_RESULT:
                return False
            elif coding.code == TRANSFER_DEGRADED:
                return False
    return True


def create_xml_table(table: ResultTable) -> dict:
    # create dict in format for xmltodict to convert to item with caption and table
    table_dict = {
        "caption": table.title,
        # list of tables to allow for appending of specimens etc
        "table": [],
    }
    result_table = {
        "thead": {"tr": {"th": table.headers}},
        "tbody": {"tr": []},
    }
    for row in table.rows:
        result_table["tbody"]["tr"].append({"td": row.cells})
    table_dict["table"].append(result_table)
    return table_dict


def degraded_original_name(observation: obs.Observation) -> str | None:
    """Return the original label only for an explicitly transfer-degraded code."""
    code = observation.code
    if (
        code
        and code.text
        and any(c.system == "http://snomed.info/sct" and c.code == TRANSFER_DEGRADED for c in code.coding or [])
    ):
        return code.text
    return None


def specimen_notes_table(report: dr.DiagnosticReport, index: dict) -> dict | None:
    """Collect notes from this report's specimens in a separate, labelled table.

    Repeat the specimen label for each note so that several samples cannot be
    confused. Deduplicate references to the same resource, not identical text
    from distinct specimens. Never use specimens from an unrelated report.
    """
    rows = []
    seen = set()
    for reference in report.specimen or []:
        specimen = index.get(reference.reference)
        if not isinstance(specimen, Specimen) or id(specimen) in seen:
            continue
        seen.add(id(specimen))
        specimen_type = specimen.type
        label = (
            specimen_type.text or next((c.display for c in specimen_type.coding or [] if c.display), None)
            if specimen_type
            else None
        )
        accession = specimen.accessionIdentifier.value if specimen.accessionIdentifier else None
        identifier = accession or next((i.value for i in specimen.identifier or [] if i.value), None) or specimen.id
        label = " — ".join(value for value in (label, identifier) if value) or "Specimen"
        collection = specimen.collection
        collected = "Not supplied"
        if collection and collection.collectedDateTime:
            collected = collection.collectedDateTime.as_json()
        elif collection and collection.collectedPeriod:
            period = collection.collectedPeriod
            collected = f"{period.start.as_json() if period.start else 'Unknown start'} – {period.end.as_json() if period.end else 'Unknown end'}"
        received = specimen.receivedTime.as_json() if specimen.receivedTime else "Not supplied"
        notes = [note.text for note in specimen.note or [] if note.text]
        for note in notes or [""]:
            rows.append({"td": [label, note, collected, received]})
    if not rows:
        return None
    return {
        "caption": "Specimen notes",
        "thead": {"tr": {"th": ["Specimen", "Notes", "Collected", "Received"]}},
        "tbody": {"tr": rows},
    }


def result_status(status: str | None, *, organizer: bool = False) -> CS:
    """Map FHIR laboratory workflow to CDA, never equating unknown with final.

    Amended/corrected/appended are changes after finalisation in FHIR STU3.
    Cancellation does not distinguish before/after activation, so use aborted.
    Result Observation's restricted status vocabulary has no nullified code;
    represent entered-in-error as OTH there and retain its source status in text.
    Organizers use ActStatus and can represent nullified directly.
    """
    mapped = {
        "registered": "active",
        "partial": "active",
        "preliminary": "active",
        "final": "completed",
        "amended": "completed",
        "corrected": "completed",
        "appended": "completed",
        "cancelled": "aborted",
    }
    if status in mapped:
        return CS(code=mapped[status])
    if status == "entered-in-error" and organizer:
        return CS(code="nullified")
    if status in (None, "", "unknown"):
        return CS(nullFlavor="UNK")
    return CS(nullFlavor="OTH")


async def create_result_component(observation: obs.Observation, group_time: IVL_TS = None) -> ResultWithRow:
    result_component = ResultObservation(
        code=code_with_translations(observation.code.coding),
        id=id_helper(observation.identifier) if observation.identifier else None,
        statusCode=result_status(observation.status),
    )

    if group_time:
        result_component.effectiveTime = group_time
    else:
        result_component.effectiveTime = (
            cda_time_interval(observation.effectiveDateTime, observation.effectiveDateTime)
            if observation.effectiveDateTime
            else None
        )
    table_row = ResultTableRow(cells=[None, None, None, None])
    original_name = degraded_original_name(observation)
    if original_name:
        # Keep the supplied generic coding and its display intact. CDA's
        # originalText carries the original test name without recoding it.
        result_component.code.originalText = original_name
    table_row.cells[0] = original_name or result_component.code.displayName

    mapped_value = map_result_value(observation)
    result_component.value = mapped_value.value
    table_row.cells[1] = mapped_value.text

    # Keep the existing numeric abnormal emphasis, without comparing absent
    # quantities or attempting to interpret comparator/text values numerically.
    quantity = observation.valueQuantity
    if quantity and quantity.value is not None and not quantity.comparator:
        outside_reference_range = any(
            (r.low is not None and r.low.value is not None and quantity.value < r.low.value)
            or (r.high is not None and r.high.value is not None and quantity.value > r.high.value)
            for r in observation.referenceRange or []
        )
        if outside_reference_range:
            table_row.cells[1] = {"content": {"@styleCode": "flagData", "#text": mapped_value.text}}

    if observation.comment:
        result_component.text = observation.comment
        comment_dict = {
            "@styleCode": "allIndent",
            "content": [
                # {"@styleCode": "cellHeader", "#text": "Comment:"},
                # {"#text": observation.comment},
                {"@styleCode": "cellHeader", "#text": observation.comment}
            ],
        }
        # content.append(comment_dict)
        table_row.cells[3] = {"content": comment_dict}

    if result_component.statusCode.nullFlavor == "OTH":
        # Keep an explicit withdrawal/unrecognised status visible even where
        # the restricted CDA result vocabulary cannot express that state.
        status_note = f"Source result status: {observation.status}"
        result_component.text = "\n".join(filter(None, (status_note, observation.comment)))
        table_row.cells[3] = {"#text": result_component.text}

    if hasattr(observation, "interpretation") and observation.interpretation:
        result_component.interpretationCode = code_with_translations(observation.interpretation.coding)

    # reference range
    if observation.referenceRange:
        observation_ranges = []
        unit = observation.valueQuantity.unit if observation.valueQuantity else None

        for reference_range in observation.referenceRange:
            if getattr(reference_range, "text", None):
                observation_ranges.append({"text": reference_range.text})

            low = getattr(reference_range, "low", None)
            high = getattr(reference_range, "high", None)
            if low or high:
                range_value = {"@xsi:type": "IVL_PQ"}
                if low:
                    range_value["low"] = {
                        "@value": low.value,
                        "@unit": unit,
                    }
                if high:
                    range_value["high"] = {
                        "@value": high.value,
                        "@unit": unit,
                    }
                observation_ranges.append({"value": range_value})

        if observation_ranges:
            # TODO: Emit separate referenceRange wrappers with exactly one
            # observationRange each (CONF:1198-7151), and supply a value for
            # text-only ranges (CONF:1198-32175).
            # TODO: Construct list[ReferenceRange] with ObservationRange models;
            # test_results emits Pydantic serializer warnings for this dict assignment.
            result_component.referenceRange = {"observationRange": observation_ranges}

            # create string with each reference range on a new line
            reference_range_str = "\n".join(
                [
                    (
                        f"{r['text']}"
                        if "text" in r
                        else (
                            f"{r['value']['low']['@value']} - {r['value']['high']['@value']} {unit}"
                            if "low" in r["value"] and "high" in r["value"]
                            else (
                                f">= {r['value']['low']['@value']} {unit}"
                                if "low" in r["value"]
                                else f"<= {r['value']['high']['@value']} {unit}"
                            )
                        )
                    )
                    for r in observation_ranges
                ]
            )
            table_row.cells[2] = {"#text": reference_range_str}

    return ResultWithRow(entry=result_component, row=table_row)


async def investigation(diagnostic_report: dr.DiagnosticReport, index: dict) -> InvestigationWithTable:
    """Render one report with explicit groups and locally associated filing comments."""

    observations: list[obs.Observation] = (
        [index[x.reference] for x in diagnostic_report.result if isinstance(index.get(x.reference), obs.Observation)]
        if diagnostic_report.result
        else []
    )

    report_issued_time = report_issue_time(diagnostic_report)
    test_group_headers = [o for o in observations if is_test_group_header(o)]

    category_observation = None
    if len(test_group_headers) == 0 or len(test_group_headers) > 1:
        test_title = "Diagnostic Report"

    else:
        test_title = test_group_headers[0].code.coding[0].display if test_group_headers else "Diagnostic Report"

        # look for category in test group header
        for category in test_group_headers[0].category or []:
            for code in category.coding or []:
                if code.system == "http://hl7.org/fhir/observation-category":
                    if code.code == "laboratory":
                        print("Category is laboratory")
                        # TODO: Supply the required id, observation code and
                        # statusCode, and the 2015-08-01 Result Observation
                        # template extension (CONF:1198-7137/7133/7134/32575).
                        # TODO: Confirm category code 16 and the additional
                        # template OID against the receiving system's specification;
                        # C-CDA recommends SNOMED CT for CD values (CONF:1198-32610).
                        category_observation = ResultObservation(
                            templateId=[
                                II(
                                    root="2.16.840.1.113883.10.20.22.4.2",
                                ),
                                II(
                                    root="1.2.840.114350.1.72.3.4",
                                ),
                            ],
                            value=CD(
                                code="16",
                                codeSystem="1.2.840.114350.1.72.1.5007",
                            ),
                            effectiveTime=report_issued_time,
                        )

    organizer = ResultsOrganizer(
        statusCode=result_status(diagnostic_report.status, organizer=True),
        id=id_helper(diagnostic_report.identifier),
        code=(code_with_translations(test_group_headers[0].code.coding) if test_group_headers else None),
        effectiveTime=organizer_collection_time(diagnostic_report, index),
        specimen=[cda_specimen(s) for s in report_specimens(diagnostic_report, index)] or None,
    )

    grouping = group_investigation(diagnostic_report, index)
    table_rows = []
    components = []
    emitted = set()

    def narrative_row(text):
        """Place a heading or annotation across the existing four columns."""
        table_rows.append(ResultTableRow(cells=[{"@colspan": 4, "#text": text}]))

    async def render(reference, path=()):
        """Render each assertion once while retaining shared group associations."""
        observation = grouping.observations[reference]
        if reference in path:
            grouping.issues.append(f"Cyclic test grouping at {reference}")
            narrative_row(f"Grouping could not be resolved for: {observation_label(observation)}")
            return
        if reference in emitted:
            narrative_row(f"Also associated: {observation_label(observation)} (shown above)")
            return
        emitted.add(reference)
        if is_filing_comment(observation):
            narrative_row(f"Filing comments: {observation_label(observation)}")
            if observation.valueString is not None:
                narrative_row(observation.valueString)
            if observation.comment is not None:
                narrative_row(observation.comment)
            return

        members = grouping.members.get(reference, [])
        has_value = has_result_value(observation)
        if members:
            narrative_row(f"Test group: {observation_label(observation)}")
            if not has_value and observation.comment:
                narrative_row(observation.comment)
            if not has_value and observation.interpretation:
                interpretation = observation.interpretation
                text = interpretation.text or "; ".join(c.display or c.code or "" for c in interpretation.coding or [])
                narrative_row(f"Group interpretation: {text}")

        # Only explicit membership makes a group. String-valued and narrative-
        # only standalone observations remain results. Keep unexpected values
        # on groups too, using the existing serializer without changing mapping.
        if not members or has_value:
            converted = await create_result_component(observation, report_issued_time)
            components.append({"observation": converted.entry})
            table_rows.append(converted.row)
        for comment in grouping.comments.get(reference, []):
            await render(comment, path + (reference,))
        for member in members:
            await render(member, path + (reference,))

    for reference, observation in grouping.observations.items():
        if reference not in grouping.parents and not is_filing_comment(observation):
            await render(reference)
    for reference in grouping.direct:
        if is_filing_comment(grouping.observations[reference]) and reference not in grouping.attached_comments:
            narrative_row("Report-level filing")
            await render(reference)
    # Cyclic and otherwise unplaced items must not disappear from the report.
    for reference in grouping.observations:
        if reference not in emitted:
            narrative_row("Unplaced source item")
            await render(reference)
    if grouping.issues:
        narrative_row("Some investigation relationships could not be resolved; available results are shown.")
        for issue in grouping.issues:
            logger.warning("Investigation %s: %s", diagnostic_report.id, issue)
    organizer.component = components
    if category_observation:
        organizer.component.append({"observation": category_observation})

    result_table = ResultTable(
        title=f"{test_title} {diagnostic_report.issued.date if diagnostic_report.issued else 'Issue time not supplied'}",
        headers=["Component", "Value", "Reference Range", "Comments"],
        rows=table_rows,
    )

    table = create_xml_table(result_table)
    if diagnostic_report.conclusion:
        # Report conclusions belong to the whole report, not to an invented
        # analyte or one particular test group. Place them before the tables.
        table = {
            "caption": table["caption"],
            "paragraph": [
                {"content": {"@styleCode": "Bold", "#text": "Report conclusion"}},
                diagnostic_report.conclusion,
            ],
            "table": table["table"],
        }
    specimen_table = specimen_notes_table(diagnostic_report, index)
    if specimen_table:
        table["table"].append(specimen_table)

    return InvestigationWithTable(
        organizer=organizer.model_dump(by_alias=True, exclude_none=True),
        table=table,
    )
