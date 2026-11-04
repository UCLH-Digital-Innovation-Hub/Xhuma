"""Section presentation regressions through the actual full-document delivery encoder."""

import base64
import datetime
import json
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest
from fhirclient.models.annotation import Annotation
from fhirclient.models.bundle import Bundle
from fhirclient.models.period import Period

from app.ccda import fhir2ccda
from app.ccda.convert_mime import base64_xml
from app.ccda.entries.types import EntryWithRow

NS = {"c": "urn:hl7-org:v3"}
MED_WIDTHS = [7, 7, 6, 10, 17, 18, 22, 13]
WIDTHS = {
    "Active Medications": MED_WIDTHS,
    "Past Medications": MED_WIDTHS,
    "Allergies, adverse reactions, alerts": [12, 18, 15, 15, 10, 30],
    "Problems List": [15, 15, 70],
    "Immunisations": [15, 50, 15, 20],
}
SCOPE = {
    "include_allergies": "Allergies and adverse reactions",
    "include_medication": "Medications and medical devices",
    "include_problems": "Problems",
    "include_investigations": "Investigations and results",
    "include_immunisations": "Immunisations",
}


def source_bundle():
    source = json.loads((Path(__file__).parent / "fixtures/bundles/9690937286.json").read_text())
    source["entry"] = [entry for entry in source["entry"] if "fhir_comments" not in entry]
    source["entry"].append(
        {
            "resource": {
                "resourceType": "Immunization",
                "id": "presentation-vaccine",
                "status": "completed",
                "notGiven": False,
                "primarySource": True,
                "patient": {"reference": "Patient/test"},
                "date": "2024-02-03",
                "vaccineCode": {"coding": [{"code": "123", "display": "Vaccine & <name>"}]},
                "lotNumber": "Lot 123",
            }
        }
    )
    for entry in source["entry"]:
        if entry["resource"].get("title") == "Immunisations":
            entry["resource"]["entry"] = [{"item": {"reference": "Immunization/presentation-vaccine"}}]
    bundle = Bundle(source)
    index = {f"{e.resource.resource_type}/{e.resource.id}": e.resource for e in bundle.entry}
    lists = {e.resource.title: e.resource for e in bundle.entry if e.resource.resource_type == "List"}
    # These tests concern section presentation, not investigation report rendering.
    bundle.entry = [e for e in bundle.entry if getattr(e.resource, "title", None) != "Investigations and results"]
    for section in lists.values():
        section.note = None
    return bundle, index, lists


async def delivery(bundle, index):
    root = ET.fromstring(base64.b64decode(base64_xml(await fhir2ccda.convert_bundle(bundle, index))))
    assert root.tag == "{urn:hl7-org:v3}ClinicalDocument"
    assert root.find("c:recordTarget", NS) is not None
    return root, {
        section.findtext("c:title", namespaces=NS): section
        for section in root.findall("c:component/c:structuredBody/c:component/c:section", NS)
    }


def text(node):
    return " ".join("".join(node.itertext()).split())


def assert_widths(section, expected):
    table = section.find("c:text/c:table", NS)
    columns = table.findall("c:colgroup/c:col", NS)
    assert [column.get("width") for column in columns] == [f"{width}%" for width in expected]
    assert sum(int(column.get("width").rstrip("%")) for column in columns) == 100
    assert len(table.findall("c:thead/c:tr/c:th", NS)) == len(expected)
    assert list(table)[0].tag == "{urn:hl7-org:v3}colgroup"
    for row in table.findall("c:tbody/c:tr", NS):
        cells = row.findall("c:td", NS)
        assert sum(int(cell.get("colspan", "1")) for cell in cells) == len(expected)


@pytest.mark.asyncio
@pytest.mark.parametrize("empty", [False, True])
async def test_table_widths_and_clinical_rows_survive_delivery(monkeypatch, empty):
    bundle, index, lists = source_bundle()
    medications = lists["Medications and medical devices"]
    medications.entry = medications.entry[:2]
    expected_rows = {}
    for reference, status in zip(medications.entry, ["active", "completed"], strict=True):
        statement = index[reference.item.reference]
        statement.status = status
        statement.effectivePeriod = Period({"end": "2000-01-01"})
        expected_rows[statement.id] = [
            "01/01/1999",
            "01/01/2000",
            status,
            "Repeat",
            "Drug & <name>",
            "Take one daily",
            "Do not stop abruptly",
            "Last issued date: 02/01/1999",
        ]

    async def render_medication(statement, resource_index):
        return EntryWithRow(entry={"source_id": statement.id}, row=expected_rows[statement.id].copy())

    monkeypatch.setattr(fhir2ccda, "medication", render_medication)
    if empty:
        for section in lists.values():
            section.entry = []
    root, sections = await delivery(bundle, index)
    for title, widths in WIDTHS.items():
        assert_widths(sections[title], widths)
        if empty:
            expected = "No information received" if title.startswith("Allergies") else "No Information Available"
            assert text(sections[title].find("c:text/c:table/c:tbody/c:tr/c:td", NS)) == expected
        else:
            assert sections[title].find("c:entry", NS) is not None
            assert sections[title].findall("c:text/c:table/c:tbody/c:tr", NS)
    if not empty:
        assert [text(cell) for cell in sections["Immunisations"].findall("c:text/c:table/c:tbody/c:tr/c:td", NS)] == [
            "03/02/2024",
            "Vaccine & <name>",
            "Lot 123",
            "completed",
        ]
        assert "Sesame oil (substance)" in text(sections["Allergies, adverse reactions, alerts"].find("c:text", NS))
        for title, row in zip(["Active Medications", "Past Medications"], expected_rows.values(), strict=True):
            assert [text(cell) for cell in sections[title].findall("c:text/c:table/c:tbody/c:tr/c:td", NS)] == row
    assert root.findall(".//*[@styleCode='flagData']") == []


@pytest.mark.asyncio
async def test_fallback_medication_widths_and_all_cell_text(monkeypatch):
    bundle, index, lists = source_bundle()
    lists["Medications and medical devices"].entry = lists["Medications and medical devices"].entry[:1]
    cells = [
        "01/01/2020",
        "02/02/2021",
        "completed",
        "Acute",
        "Drug & <name>",
        "Take with food",
        "Clinical note",
        "Issued yesterday",
    ]

    def fail_split(original):
        raise ValueError("Exercise unsplit fallback")

    async def render_medication(statement, resource_index):
        return EntryWithRow(entry={"source_id": statement.id}, row=cells.copy())

    monkeypatch.setattr(fhir2ccda, "copy", fail_split)
    monkeypatch.setattr(fhir2ccda, "medication", render_medication)
    _, sections = await delivery(bundle, index)
    assert "Active Medications" not in sections
    assert "Past Medications" not in sections
    assert_widths(sections["Medications"], MED_WIDTHS)
    assert [text(cell) for cell in sections["Medications"].findall("c:text/c:table/c:tbody/c:tr/c:td", NS)] == cells


@pytest.mark.asyncio
async def test_notices_are_ordinary_text_except_conversion_warning(monkeypatch):
    bundle, index, lists = source_bundle()
    for section in lists.values():
        section.entry = []
    confidentiality = "Confidentiality: information withheld at the patient's request & <restricted>."
    provider = "Provider warning: information not available from the GP system."
    lists["Allergies and adverse reactions"].note = [
        Annotation({"text": confidentiality}),
        Annotation({"text": provider}),
    ]
    _, sections = await delivery(bundle, index)
    paragraph = sections["Allergies, adverse reactions, alerts"].find("c:text/c:paragraph", NS)
    assert paragraph.attrib == {}
    assert paragraph.findall(".//*[@styleCode]") == []
    assert text(paragraph) == "Some allergies may be recorded as problems." + confidentiality + provider
    assert len(paragraph.findall("c:br", NS)) == 3
    ordinary = sections["Immunisations"].find("c:text/c:paragraph", NS)
    assert ordinary.attrib == {} and text(ordinary) == "" and list(ordinary) == []

    # Keep a successfully converted clinical row beside a deliberate failed item.
    bundle, index, lists = source_bundle()
    for title, section in lists.items():
        if title != "Problems":
            section.entry = []
    problems = lists["Problems"]
    problems.entry = problems.entry[:2]
    assert len(problems.entry) == 2
    problems.note = [Annotation({"text": provider})]
    failed_id = index[problems.entry[0].item.reference].id
    original = fhir2ccda.problem
    survivor = original(index[problems.entry[1].item.reference])

    def convert_problem(resource):
        if resource.id == failed_id:
            raise ValueError("Deliberate conversion failure")
        return original(resource)

    monkeypatch.setattr(fhir2ccda, "problem", convert_problem)
    _, sections = await delivery(bundle, index)
    section = sections["Problems List"]
    paragraph = section.find("c:text/c:paragraph", NS)
    warning = paragraph.find("c:content", NS)
    assert paragraph.attrib == {}
    assert warning.attrib == {"styleCode": "Bold"}
    assert text(warning) == (
        "CLINICAL WARNING: 1 item(s) in this section could not be safely converted and have been omitted. "
        "Please refer directly to alternative systems for complete data."
    )
    assert warning.find("c:br", NS) is not None
    assert provider in text(paragraph)
    assert provider not in text(warning)
    assert paragraph.findall(".//*[@styleCode]") == [warning]
    cells = section.findall("c:text/c:table/c:tbody/c:tr/c:td", NS)
    assert [text(cell) for cell in cells] == [survivor.row[0], survivor.row[1], survivor.row[2]["content"]["#text"]]
    assert len(section.findall("c:entry", NS)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "included", [set(SCOPE), set(), {"include_allergies", "include_problems"}], ids=["all", "none", "mixed"]
)
@pytest.mark.parametrize("cache_period", [None, "48 hours"], ids=["default", "override"])
async def test_important_information_retains_every_sentence_with_only_labels_bold(monkeypatch, included, cache_period):
    class FixedDateTime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 4, 12, 34, 56, tzinfo=tz)

    monkeypatch.setattr(fhir2ccda, "datetime", SimpleNamespace(datetime=FixedDateTime, date=datetime.date))
    monkeypatch.setattr(fhir2ccda, "get_gp_connect_inclusions", lambda: {key: key in included for key in SCOPE})
    monkeypatch.delenv("CCDA_CACHING_PERIOD", raising=False)
    if cache_period is not None:
        monkeypatch.setenv("CCDA_CACHING_PERIOD", cache_period)
    bundle, index, lists = source_bundle()
    for section in lists.values():
        section.entry = []
    root, sections = await delivery(bundle, index)
    paragraphs = sections["Important Information"].findall("c:text/c:paragraph", NS)
    included_names = ", ".join(name for key, name in SCOPE.items() if key in included) or "None"
    omitted_names = ", ".join(name for key, name in SCOPE.items() if key not in included)
    expected = [
        ("Generated:", "C-CDA generated by Xhuma from GP Connect on 04-11-2026"),
        (None, "This record contains information from the patient's GP record. It was generated on 2026-11-04."),
        ("Scope:", f"IMPORTANT: This document ONLY contains information regarding: {included_names}."),
    ]
    if omitted_names:
        expected.append(
            ("Not included:", f"All other clinical information (such as {omitted_names}) should be sought elsewhere.")
        )
    expected.append(
        ("Freshness:", f"Information added to the record in the last {cache_period or '24 hours'} may be missing.")
    )
    assert len(paragraphs) == len(expected)
    for paragraph, (label, sentence) in zip(paragraphs, expected, strict=True):
        assert paragraph.attrib == {}
        assert text(paragraph) == (f"{label} {sentence}" if label else sentence)
        styled = paragraph.findall(".//*[@styleCode]")
        if label:
            contents = paragraph.findall("c:content", NS)
            assert len(contents) == 2
            assert styled == [contents[0]]
            assert contents[0].attrib == {"styleCode": "Bold"}
            assert contents[0].text == label
            assert contents[1].attrib == {}
            assert text(contents[1]) == sentence
        else:
            assert styled == []
    assert root.findall(".//c:footnote", NS) == []
    assert root.findall(".//*[@styleCode='flagData']") == []
    assert root.find("c:effectiveTime", NS).get("value") == "20261104123456"
