import asyncio

from entries import EntryWithRow
from fhirclient.models import diagnosticreport as dr
from fhirclient.models import observation as obs
from helpers import code_with_translations, effective_time_helper
from models.base import ResultObservation, ResultsOrganizer

COMMENT_NOTE_SNOMED = "37331000000100"


def is_comment_note(observation: obs.Observation) -> bool:
    if observation.code and observation.code.coding:
        for coding in observation.code.coding:
            if coding.code == COMMENT_NOTE_SNOMED:
                return True
    return False


def is_test_group_header(observation: obs.Observation) -> bool:
    # test group headers have no value and are not comment notes

    if is_comment_note(observation):
        return False
    if observation.value:
        return False
    return True


async def create_result_component(observation: obs.Observation) -> ResultObservation:
    result_component = ResultObservation(code=code_with_translations(observation.code))
    return result_component


async def investigation(
    diagnostic_report: dr.DiagnosticReport, index: dict
) -> EntryWithRow:

    observations: list[obs.Observation] = (
        [index[x] for x in diagnostic_report.result] if diagnostic_report.result else []
    )
    comment_observations = [o for o in observations if is_comment_note(o)]
    test_group_headers = [o for o in observations if is_test_group_header(o)]

    # add results in test group headers to observations list
    for header in test_group_headers:
        if hasattr(header, "hasMember"):
            for member in header.hasMember:
                observations.append(index[member.reference])

    # remaining observations are test results
    test_results = [
        o
        for o in observations
        if not is_comment_note(o) and not is_test_group_header(o)
    ]

    organizer = ResultsOrganizer()
    organizer.effectiveTime = effective_time_helper(diagnostic_report.effectiveDateTime)
    organizer.component = asyncio.gather(
        *[create_result_component(o) for o in test_results]
    )

    entries = []
    if diagnostic_report.result:
        for result in diagnostic_report.result:
            observation = await obs.Observation.read_from(result.reference)
            if not is_comment_note(observation):
                entries.append(
                    EntryWithRow(
                        entry=await create_result_component(observation), row=0
                    )
                )
    return organizer, entries
