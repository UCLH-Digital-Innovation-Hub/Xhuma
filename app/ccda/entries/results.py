import logging
from dataclasses import dataclass

from fhirclient.models import diagnosticreport as dr
from fhirclient.models import observation as obs
from fhirclient.models.specimen import Specimen

from app.telemetry import measure

from ..helpers import (
    cda_time_interval,
    code_with_translations,
    id_helper,
)
from ..models.base import ResultObservation, ResultsOrganizer
from ..models.datatypes import CD, CS, II, IVL_TS
from .investigation_grouping import (
    InvestigationGraph,
    group_investigation,
    has_result_value,
    is_filing_comment,
    observation_label,
)
from .result_context import cda_specimen, organizer_collection_time, report_issue_time, report_specimens
from .result_presentation import column_widths, laboratory_details_table, report_heading_time, report_identity
from .result_ranges import outside_reference_range, reference_range
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


def create_xml_table(table: ResultTable, *, caption: str | None = None) -> dict:
    """Build a report wrapper containing one independently readable result table."""
    result_table = {
        **({"caption": {"@styleCode": "Bold", "#text": caption}} if caption else {}),
        "colgroup": column_widths(30, 20, 20, 30),
        "thead": {"tr": {"th": table.headers}},
        "tbody": {"tr": [{"td": row.cells} for row in table.rows]},
    }
    return {"caption": table.title, "table": [result_table]}


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
        "colgroup": column_widths(30, 30, 20, 20),
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

    if outside_reference_range(observation):
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

    if observation.referenceRange:
        mapped_ranges = [reference_range(r) for r in observation.referenceRange]
        result_component.referenceRange = [model for model, _ in mapped_ranges]
        table_row.cells[2] = {"#text": "\n".join(text for _, text in mapped_ranges if text)}

    return ResultWithRow(entry=result_component, row=table_row)


async def investigation(
    diagnostic_report: dr.DiagnosticReport, index: dict, graph: InvestigationGraph | None = None
) -> InvestigationWithTable:
    """Render one report with explicit groups and locally associated filing comments."""

    observations: list[obs.Observation] = (
        [index[x.reference] for x in diagnostic_report.result if isinstance(index.get(x.reference), obs.Observation)]
        if diagnostic_report.result
        else []
    )

    report_issued_time = report_issue_time(diagnostic_report)
    test_group_headers = [o for o in observations if is_test_group_header(o)]

    category_observation = None
    # Keep legacy category metadata separate from graph-based report identity.
    if len(test_group_headers) == 1:
        # look for category in test group header
        for category in test_group_headers[0].category or []:
            for code in category.coding or []:
                if code.system == "http://hl7.org/fhir/observation-category":
                    if code.code == "laboratory":
                        # print("Category is laboratory")
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

    with measure("investigations.group") as span:
        grouping = group_investigation(diagnostic_report, graph if graph is not None else index)
        span.set_attribute("report.observations", len(grouping.observations))
        span.set_attribute("report.grouping_issues", len(grouping.issues))
    test_title, report_code, panel = report_identity(
        diagnostic_report, grouping, test_group_headers[0] if len(test_group_headers) == 1 else None
    )

    organizer = ResultsOrganizer(
        statusCode=result_status(diagnostic_report.status, organizer=True),
        id=id_helper(diagnostic_report.identifier),
        code=report_code,
        effectiveTime=organizer_collection_time(diagnostic_report, index),
        specimen=[cda_specimen(s) for s in report_specimens(diagnostic_report, index)] or None,
    )

    with measure("investigations.render"):
        table_rows = []
        group_tables = []
        other_rows, unplaced_rows = [], []
        filing_comments = []
        components = []
        emitted = set()
        has_unplaced_result = False
        has_cycle = False

        def narrative_row(text):
            """Place a heading or annotation across the existing four columns."""
            table_rows.append(ResultTableRow(cells=[{"@colspan": 4, "#text": text}]))

        def group_heading(text, depth):
            content = {"@styleCode": "Bold", "#text": text}
            for _ in range(depth):
                content = {"@styleCode": "allIndent", "content": content}
            table_rows.append(ResultTableRow(cells=[{"@colspan": 4, "content": content}]))

        async def render(reference, path=(), *, table_root=False):
            """Render each assertion once while retaining shared group associations."""
            nonlocal has_cycle
            observation = grouping.observations[reference]
            if reference in path:
                has_cycle = True
                grouping.issues.append(f"Cyclic test grouping at {reference}")
                narrative_row(f"Grouping could not be resolved for: {observation_label(observation)}")
                return
            if reference in emitted:
                narrative_row(f"Also associated: {observation_label(observation)} (shown elsewhere in this report)")
                return
            emitted.add(reference)
            if is_filing_comment(observation):
                narrative_row(f"Comment: {observation_label(observation)}")
                if observation.valueString is not None:
                    narrative_row(observation.valueString)
                if observation.comment is not None:
                    narrative_row(observation.comment)
                return

            members = grouping.members.get(reference, [])
            has_value = has_result_value(observation)
            if members:
                if not table_root:
                    group_heading(f"Test group: {observation_label(observation)}", len(path))
                if not has_value and observation.comment:
                    narrative_row(observation.comment)
                if not has_value and observation.interpretation:
                    interpretation = observation.interpretation
                    text = interpretation.text or "; ".join(
                        c.display or c.code or "" for c in interpretation.coding or []
                    )
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

        # Render in the original root order to preserve structured component
        # order and shared-result ownership, collecting separate narrative tables.
        for reference in grouping.roots:
            if grouping.members.get(reference):
                table_rows = []
                group_tables.append((reference, table_rows))
                await render(reference, table_root=True)
            else:
                table_rows = other_rows
                await render(reference)
        for reference in grouping.report_comments:
            if reference not in emitted:
                emitted.add(reference)
                comment = grouping.observations[reference]
                filing_comments.append(
                    {
                        "paragraph": [
                            {"content": {"@styleCode": "Bold", "#text": observation_label(comment)}},
                            *[text for text in (comment.valueString, comment.comment) if text is not None],
                        ]
                    }
                )
        table_rows = unplaced_rows
        # Cyclic and otherwise unplaced items must not disappear from the report.
        for reference in grouping.observations:
            if reference not in emitted:
                if not is_filing_comment(grouping.observations[reference]):
                    has_unplaced_result = True
                narrative_row("Unplaced source item")
                await render(reference)
        if grouping.issues:
            for issue in grouping.issues:
                logger.warning("Investigation %s: %s", diagnostic_report.id, issue)
        # A sole explicit panel can be a BATTERY; mixed, cyclic or unplaced
        # results remain a CLUSTER. Source values do not establish membership.
        organizer.classCode = (
            "BATTERY" if panel and not has_cycle and not has_unplaced_result and not grouping.issues else "CLUSTER"
        )
        if panel and organizer.classCode == "CLUSTER":
            test_title, organizer.code, _ = report_identity(
                diagnostic_report,
                grouping,
                test_group_headers[0] if len(test_group_headers) == 1 else None,
                allow_panel=False,
            )
        organizer.component = components
        if category_observation:
            organizer.component.append({"observation": category_observation})

        title = f"{test_title} ({report_heading_time(diagnostic_report, index)})"
        narrative_tables = []

        def add_table(rows, caption):
            if rows:
                result_table = ResultTable(
                    title=title,
                    headers=["Component", "Value", "Reference Range", "Comments"],
                    rows=rows,
                )
                narrative_tables.extend(create_xml_table(result_table, caption=caption)["table"])

        for reference, rows in group_tables:
            label = observation_label(grouping.observations[reference])
            # A single complete panel already names the report; don't repeat it.
            caption = None if organizer.classCode == "BATTERY" and label == test_title else label
            add_table(rows, caption)
        add_table(other_rows, "Other results")
        add_table(unplaced_rows, "Unplaced source items")
        paragraphs = []
        if diagnostic_report.conclusion:
            paragraphs.extend(
                [
                    {"content": {"@styleCode": "Bold", "#text": "Report Interpretation"}},
                    diagnostic_report.conclusion,
                ]
            )
        if grouping.issues:
            paragraphs.append("Some investigation relationships could not be resolved; available results are shown.")
        table = {
            "caption": title,
            **({"paragraph": paragraphs} if paragraphs else {}),
            **(
                {
                    "list": {
                        "@listType": "unordered",
                        "caption": {"@styleCode": "Bold", "#text": "Report-level filing"},
                        "item": filing_comments,
                    }
                }
                if filing_comments
                else {}
            ),
            "table": narrative_tables,
        }
        specimen_table = specimen_notes_table(diagnostic_report, index)
        if specimen_table:
            table["table"].append(specimen_table)

        laboratory_table = laboratory_details_table(diagnostic_report, grouping, index)
        if laboratory_table:
            table["table"].append(laboratory_table)

        return InvestigationWithTable(
            organizer=organizer.model_dump(by_alias=True, exclude_none=True),
            table=table,
        )
