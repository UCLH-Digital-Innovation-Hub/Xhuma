import asyncio
from dataclasses import dataclass

from fhirclient.models import diagnosticreport as dr
from fhirclient.models import observation as obs

from .entries import EntryWithRow
from .helpers import (
    code_with_translations,
    datetime_helper,
    id_helper,
)
from .models.base import ResultObservation, ResultsOrganizer
from .models.datatypes import CD, CS, II, IVL_TS, IVXB_TS, PQ

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
    organizer: ResultsOrganizer
    table: dict


def is_comment_note(observation: obs.Observation) -> bool:
    if observation.code and observation.code.coding:
        for coding in observation.code.coding:
            if coding.code in COMMENT_NOTE_SNOMED:
                return True
    return False


def is_test_group_header(observation: obs.Observation) -> bool:
    # test group headers have no value and are not comment notes

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


async def create_result_component(
    observation: obs.Observation, group_time: IVL_TS = None
) -> ResultWithRow:
    result_component = ResultObservation(
        code=code_with_translations(observation.code.coding),
        id=id_helper(observation.identifier) if observation.identifier else None,
        statusCode=CS(code=observation.status) if observation.status else None,
    )

    # change final to completed for better mapping to CDA status codes
    if result_component.statusCode and result_component.statusCode.code == "final":
        result_component.statusCode.code = "Completed"

    if group_time:
        result_component.effectiveTime = group_time
    else:
        result_component.effectiveTime = (
            IVL_TS(low=IVXB_TS(value=datetime_helper(observation.effectiveDateTime)))
            if observation.effectiveDateTime
            else None
        )
    table_row = ResultTableRow(cells=[None, None, None, None])
    table_row.cells.insert(0, result_component.code.displayName)

    # block for value/comment

    if observation.valueString:
        result_component.value = {"@value": observation.valueString}
        table_row.cells.insert(1, observation.valueString)

    elif observation.valueQuantity:
        vq = observation.valueQuantity
        # Handle comparator logic
        if getattr(vq, "comparator", None):
            # comparator means IVL_PQ
            value = {"@xsi:type": "IVL_PQ"}
            if "<" in vq.comparator:
                value["high"] = {
                    "@value": vq.value,
                    "@unit": vq.unit,
                }
                if "=" in vq.comparator:
                    value["high"]["@inclusive"] = "true"
                # lower bound for physical measurement is 0
                value["low"] = {
                    "@value": 0,
                    "@unit": vq.unit,
                    "@inclusive": "true",
                }
            elif ">" in vq.comparator:
                value["low"] = {
                    "@value": vq.value,
                    "@unit": vq.unit,
                }
                if "=" in vq.comparator:
                    value["low"]["@inclusive"] = "true"
                # high bound for greater than physical measurement is infinity
                value["high"] = {"@nullFlavor": "PINF"}
            result_component.value = value
            table_row.cells.insert(
                1, f"{vq.comparator} {vq.value} {vq.unit if vq.unit else ''}"
            )
        else:
            result_component.value = PQ(
                value=vq.value,
                unit=(vq.unit if vq.unit else None),
            )
            value_text = f"{vq.value} {vq.unit if vq.unit else ''}"

            outside_reference_range = False
            if observation.referenceRange:
                has_numeric_range = False
                for reference_range in observation.referenceRange:
                    low = getattr(reference_range, "low", None)
                    high = getattr(reference_range, "high", None)
                    low_value = getattr(low, "value", None)
                    high_value = getattr(high, "value", None)

                    # Handle single-bound ranges
                    if low_value is not None and high_value is None:
                        has_numeric_range = True
                        if vq.value < low_value:
                            outside_reference_range = True
                            break
                    elif high_value is not None and low_value is None:
                        has_numeric_range = True
                        if vq.value > high_value:
                            outside_reference_range = True
                            break
                    elif low_value is not None and high_value is not None:
                        has_numeric_range = True
                        if not (low_value <= vq.value <= high_value):
                            outside_reference_range = True
                            break

                if not has_numeric_range:
                    outside_reference_range = False

            table_row.cells.insert(
                1,
                (
                    {"content": {"@styleCode": "flagData", "#text": value_text}}
                    if outside_reference_range
                    else value_text
                ),
            )

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
        table_row.cells.insert(3, {"content": comment_dict})

    # table_row.cells.insert(1, {"content": content})

    if hasattr(observation, "interpretation") and observation.interpretation:
        result_component.interpretationCode = code_with_translations(
            observation.interpretation.coding
        )

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
            table_row.cells.insert(2, {"#text": reference_range_str})

    return ResultWithRow(entry=result_component, row=table_row)


async def investigation(
    diagnostic_report: dr.DiagnosticReport, index: dict
) -> InvestigationWithTable:

    observations: list[obs.Observation] = (
        [index[x.reference] for x in diagnostic_report.result]
        if diagnostic_report.result
        else []
    )

    report_issued_time = IVL_TS(
        low=IVXB_TS(value=datetime_helper(diagnostic_report.issued))
    )
    comment_observations = [o for o in observations if is_comment_note(o)]
    test_group_headers = [o for o in observations if is_test_group_header(o)]

    # add results in test group headers to observations list
    for header in test_group_headers:
        if hasattr(header, "hasMember"):
            for member in header.hasMember:
                observations.append(index[member.reference])

    category_observation = None
    if len(test_group_headers) == 0 or len(test_group_headers) > 1:
        test_title = "Diagnostic Report"
        # treat all non comments as test results and ignore test group headers
        test_results = [o for o in observations if not is_comment_note(o)]
    else:
        test_title = (
            test_group_headers[0].code.coding[0].display
            if test_group_headers
            else "Diagnostic Report"
        )

        # look for category in test group header
        for category in test_group_headers[0].category:
            for code in category.coding:
                if code.system == "http://hl7.org/fhir/observation-category":
                    if code.code == "laboratory":
                        print("Category is laboratory")
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

        # remaining observations are test results
        test_results = [
            o
            for o in observations
            if not is_comment_note(o) and not is_test_group_header(o)
        ]

    organizer = ResultsOrganizer(
        statusCode=(
            CS(code=diagnostic_report.status) if diagnostic_report.status else None
        ),
        id=id_helper(diagnostic_report.identifier),
        code=(
            code_with_translations(test_group_headers[0].code.coding)
            if test_group_headers
            else None
        ),
        # this should be the specimen collection time if available, but for now will use report issued time
        effectiveTime=report_issued_time,
    )

    # change final to completed for better mapping to CDA status codes
    if organizer.statusCode and organizer.statusCode.code == "final":
        organizer.statusCode.code = "Completed"
    result_components = asyncio.gather(
        *[create_result_component(o, report_issued_time) for o in test_results]
    )
    organizer.component = [{"observation": c.entry} for c in await result_components]
    if category_observation:
        organizer.component.append({"observation": category_observation})
    table_rows = [c.row for c in await result_components]

    for comment in comment_observations:
        comment_row = ResultTableRow(cells=[{"@colspan": 4, "#text": comment.comment}])
        if comment.valueString:
            table_rows.append(
                ResultTableRow(cells=[{"@colspan": 4, "#text": comment.valueString}])
            )
        table_rows.append(comment_row)

    result_table = ResultTable(
        title=f"{test_title} {diagnostic_report.issued.date}",
        headers=["Component", "Value", "Reference Range", "Comments"],
        rows=table_rows,
    )

    return InvestigationWithTable(
        organizer=organizer.model_dump(by_alias=True, exclude_none=True),
        table=create_xml_table(result_table),
    )
