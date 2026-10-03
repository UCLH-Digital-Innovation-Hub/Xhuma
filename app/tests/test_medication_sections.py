"""Medication splitting preserves source lists without copying their owning bundle."""

import json
from pathlib import Path

import pytest
from fhirclient.models.annotation import Annotation
from fhirclient.models.bundle import Bundle
from fhirclient.models.period import Period

from app.ccda import fhir2ccda
from app.ccda.entries.types import EntryWithRow


class UncopyableBundleState:
    def __deepcopy__(self, memo):
        raise AssertionError("Section splitting must not copy the owning bundle")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "statuses, future, active_count, past_count",
    [
        ([], False, 0, 0),
        (["active", "active"], False, 2, 0),
        (["completed", "completed"], False, 0, 2),
        (["active", "completed"], False, 1, 1),
        (["completed", "completed"], True, 2, 0),
    ],
)
async def test_medication_sections_preserve_source_and_avoid_bundle_copy(
    monkeypatch, statuses, future, active_count, past_count
):
    source = json.loads((Path(__file__).parent / "fixtures/bundles/9690937472.json").read_text())
    source["entry"] = [entry for entry in source["entry"] if "fhir_comments" not in entry]
    bundle = Bundle(source)
    index = {f"{e.resource.resource_type}/{e.resource.id}": e.resource for e in bundle.entry}
    medications = next(
        e.resource for e in bundle.entry if getattr(e.resource, "title", None) == "Medications and medical devices"
    )
    medications.entry = (medications.entry or [])[: len(statuses)]
    assert len(medications.entry) == len(statuses)
    medications.note = [Annotation({"text": "Source medication warning"})]
    originals = [index[e.item.reference] for e in medications.entry]
    for statement, status in zip(originals, statuses, strict=True):
        statement.status = status
        statement.effectivePeriod = Period({"end": "2099-01-01" if future else "2000-01-01"})
    source_list = medications.as_json()
    source_entries, source_notes = medications.entry, medications.note
    bundle._copy_guard = UncopyableBundleState()
    converted = []

    async def render_medication(statement, resource_index):
        # Isolate section splitting from dose mapping and external terminology I/O.
        assert resource_index is index
        assert any(statement is original for original in originals)
        converted.append(statement.id)
        return EntryWithRow(entry={"source_id": statement.id}, row=["", "", statement.status, "", "Drug", "", "", ""])

    monkeypatch.setattr(fhir2ccda, "medication", render_medication)
    result = await fhir2ccda.convert_bundle(bundle, index)
    sections = [
        item["section"]
        for item in result["ClinicalDocument"]["component"]["structuredBody"]["component"]
        if item["section"]["code"]["@code"] == "10160-0"
    ]
    assert [section["title"] for section in sections] == ["Active Medications", "Past Medications"]
    for section, expected_count in zip(sections, [active_count, past_count], strict=True):
        assert "Source medication warning" in section["text"]["paragraph"]["#text"]
        if expected_count:
            assert len(section["entry"]) == expected_count
            assert len(section["text"]["table"]["tbody"]["tr"]) == expected_count
        else:
            assert section["text"]["table"]["tbody"]["tr"]["td"]["#text"] == "No Information Available"
    active_ids = [statement.id for statement in originals if statement.status == "active" or future]
    if active_ids:
        assert [entry["source_id"] for entry in sections[0]["entry"]] == active_ids
    if future:
        assert "2 medications marked as complete" in sections[0]["text"]["paragraph"]["#text"]
    assert sorted(converted) == sorted(statement.id for statement in originals)
    assert medications.as_json() == source_list
    assert medications.entry is source_entries
    assert medications.note is source_notes
