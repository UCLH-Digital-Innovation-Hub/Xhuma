import asyncio
from dataclasses import dataclass

from fhirclient.models import diagnosticreport as dr
from fhirclient.models import observation as obs

from app import logging

from .entries import EntryWithRow
from .helpers import (
    code_with_translations,
    datetime_helper,
    effective_time_helper,
    id_helper,
)
from .models.base import ResultObservation, ResultsOrganizer
from .models.datatypes import CS, IVL_TS, IVXB_TS, PQ

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


async def create_result_component(observation: obs.Observation) -> ResultWithRow:
    result_component = ResultObservation(
        code=code_with_translations(observation.code.coding),
        id=id_helper(observation.identifier) if observation.identifier else None,
    )
    table_row = ResultTableRow(cells=[])
    table_row.cells.append(result_component.code.displayName)

    # block for value/comment
    content = []
    if observation.valueQuantity:

        result_component.value = PQ(
            value=observation.valueQuantity.value,
            unit=(
                observation.valueQuantity.unit
                if observation.valueQuantity.unit
                else None
            ),
        )
        content.append(
            f"{observation.valueQuantity.value} {observation.valueQuantity.unit if observation.valueQuantity.unit else ''}"
        )

    if observation.comment:
        comment_dict = {
            "@styleCode": "allIndent",
            "content": [
                {"@styleCode": "cellHeader", "#text": "Comment:"},
                {"#text": observation.comment},
            ],
        }
        content.append(comment_dict)

    table_row.cells.append(content)

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
            table_row.cells.append(reference_range_str)

    return ResultWithRow(entry=result_component, row=table_row)


async def investigation(
    diagnostic_report: dr.DiagnosticReport, index: dict
) -> InvestigationWithTable:

    observations: list[obs.Observation] = (
        [index[x.reference] for x in diagnostic_report.result]
        if diagnostic_report.result
        else []
    )
    comment_observations = [o for o in observations if is_comment_note(o)]
    test_group_headers = [o for o in observations if is_test_group_header(o)]

    # add results in test group headers to observations list
    for header in test_group_headers:
        if hasattr(header, "hasMember"):
            for member in header.hasMember:
                observations.append(index[member.reference])

    if len(test_group_headers) > 1:
        test_title = "Diagnostic Report"
        # treat all non comments as test results and ignore test group headers
        test_results = [o for o in observations if not is_comment_note(o)]
    else:
        test_title = (
            test_group_headers[0].code.coding[0].display
            if test_group_headers
            else "Diagnostic Report"
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
    )
    organizer.effectiveTime = (
        IVL_TS(low=IVXB_TS(value=datetime_helper(diagnostic_report.issued)))
        if diagnostic_report.effectiveDateTime
        else None
    )
    result_components = asyncio.gather(
        *[create_result_component(o) for o in test_results]
    )
    organizer.component = [c.entry for c in await result_components]
    table_rows = [c.row for c in await result_components]

    result_table = ResultTable(
        title=f"{test_title} {diagnostic_report.issued.date}",
        headers=["Component", "Value/Comment", "Reference Range"],
        rows=table_rows,
    )

    print(result_table)
    return InvestigationWithTable(
        organizer=organizer, table=create_xml_table(result_table)
    )
