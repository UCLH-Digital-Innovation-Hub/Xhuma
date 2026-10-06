"""Verify specimen participation and Epic collection/finalising-time separation."""

import pytest
import xmltodict
from fhirclient.models.fhirdate import FHIRDate
from fhirclient.models.fhirreference import FHIRReference
from fhirclient.models.specimen import Specimen
from pydantic import ValidationError

from app.ccda.entries.result_context import organizer_collection_time
from app.ccda.entries.results import investigation
from app.ccda.helpers import fhir_to_cda_timestamp
from app.ccda.models.admin import AssignedAuthor, AuthorParticipation
from app.ccda.models.base import ResultOrganizerTime, ResultsOrganizer
from app.ccda.models.datatypes import II, IVXB_TS, TS
from app.tests.test_investigation_grouping import observation, report_and_index


@pytest.mark.parametrize(
    "source,expected",
    [
        ("2024", "2024"),
        ("2024-02", "202402"),
        ("2024-02-03", "20240203"),
        ("2024-02-03T14:15:16+01:00", "20240203141516+0100"),
        ("2024-02-03T14:15:16.123Z", "20240203141516.123+0000"),
    ],
)
def test_timestamps_preserve_source_precision_and_offset(source, expected):
    assert fhir_to_cda_timestamp(FHIRDate(source)) == expected


def add_specimen(report, index, name, collection):
    """Attach a sample with identifiable material and separate received time."""
    sample = Specimen(
        {
            "resourceType": "Specimen",
            "id": name,
            "subject": {"reference": "Patient/test"},
            "type": {"text": "Serum"},
            "identifier": [{"system": "https://example.test/specimen", "value": name}],
            "collection": collection,
            "receivedTime": "2024-02-04T09:00:00Z",
        }
    )
    index[f"Specimen/{name}"] = sample
    report.specimen = (report.specimen or []) + [FHIRReference({"reference": f"Specimen/{name}"})]


@pytest.mark.asyncio
async def test_collection_time_differs_from_shared_component_issue_time():
    report, index = report_and_index([observation("a", valueString="A"), observation("b", valueString="B")], ["a", "b"])
    report.issued = FHIRDate("2024-02-05T10:11:12+00:00")
    add_specimen(report, index, "sample", {"collectedDateTime": "2024-02-03T14:15:16+01:00"})
    output = await investigation(report, index)
    assert output.organizer["effectiveTime"]["low"]["@value"] == "20240203141516+0100"
    assert output.organizer["effectiveTime"]["high"]["@value"] == "20240203141516+0100"
    for component in output.organizer["component"]:
        time = component["observation"]["effectiveTime"]
        assert time["low"]["@value"] == time["high"]["@value"] == "20240205101112+0000"
    sample = output.organizer["specimen"][0]
    assert sample["@typeCode"] == "SPC"
    assert sample["specimenRole"]["@classCode"] == "SPEC"
    assert sample["specimenRole"]["specimenPlayingEntity"]["code"]["originalText"] == "Serum"
    assert "2024-02-04T09:00:00" in str(output.table)


def test_collection_period_unknown_and_conflicting_samples():
    report, index = report_and_index([], [])
    assert organizer_collection_time(report, index).low.nullFlavor == "UNK"
    add_specimen(report, index, "one", {"collectedPeriod": {"start": "2024-02-03", "end": "2024-02-04"}})
    time = organizer_collection_time(report, index)
    assert time.low.value == "20240203" and time.high.value == "20240204"
    add_specimen(report, index, "two", {"collectedDateTime": "2024-02-06"})
    time = organizer_collection_time(report, index)
    assert time.low.nullFlavor == time.high.nullFlavor == "UNK"
    assert time.low.value is None


@pytest.mark.asyncio
async def test_missing_issue_time_does_not_borrow_component_or_collection_time():
    report, index = report_and_index([observation("a", valueString="A", effectiveDateTime="2024-02-02")], ["a"])
    report.issued = None
    add_specimen(report, index, "one", {"collectedDateTime": "2024-02-03"})
    output = await investigation(report, index)
    assert output.organizer["component"][0]["observation"]["effectiveTime"]["low"]["@nullFlavor"] == "UNK"


def test_multiple_authors_and_required_organizer_time_bounds():
    authors = [
        AuthorParticipation(
            assignedAuthor=AssignedAuthor(id=[II(root="1.2.3", extension=n)]), time=TS(**{"@value": "20240205"})
        )
        for n in ("one", "two")
    ]
    organizer = ResultsOrganizer(author=authors)
    data = organizer.model_dump(by_alias=True, exclude_none=True)
    assert len(data["author"]) == 2
    xml = xmltodict.unparse({"organizer": data})
    assert xml.count("<author>") == 2
    restored = ResultsOrganizer.model_validate(data)
    assert len(restored.author) == 2
    with pytest.raises(ValidationError):
        ResultOrganizerTime(low=IVXB_TS(value="20240203"))
