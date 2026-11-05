"""Serialized result-only caption and source-prose presentation contracts."""

import base64
from email import message_from_string
from types import SimpleNamespace

import pytest
import xmltodict
from defusedxml import ElementTree as ET
from fhirclient.models.codeableconcept import CodeableConcept
from fhirclient.models.fhirdate import FHIRDate

from app.ccda.convert_mime import base64_xml, convert_mime
from app.ccda.entries.result_presentation import display_time, narrative_paragraphs
from app.ccda.entries.results import investigation
from app.tests.test_investigation_grouping import observation, report_and_index
from app.tests.test_result_context import add_specimen


def delivered(output, transport):
    document = {"item": output.table}
    xml = (
        base64.b64decode(base64_xml(document))
        if transport == "base64"
        else message_from_string(convert_mime(document)).get_payload(0).get_payload()
    )
    return ET.fromstring(xml)


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["base64", "mime"])
@pytest.mark.parametrize(
    "labels,expected",
    [
        (["Blood count", "Renal function"], "Blood count; Renal function"),
        ([" OTHER  results ", "Unplaced SOURCE items", "Report-level filing", " Comment note "], ""),
        ([" SUPPLIED  title ", "Renal"], "Renal"),
        (["Renal  function", " renal function ", "RENAL FUNCTION", "Blood"], "Renal  function; Blood"),
        (["A", "B", "C", "D", "E", "F", " f "], "A; B; C; D; + 2 more groups"),
        ([], ""),
    ],
)
async def test_explicit_root_summary_preserves_plain_date_first_caption(transport, labels, expected):
    nodes = []
    direct = []
    for number, label in enumerate(labels):
        name = f"panel{number}"
        nodes.extend(
            [
                observation(name, [("has-member", f"value{number}")], code={"text": label}),
                observation(f"value{number}", valueQuantity={"value": number}),
            ]
        )
        direct.append(name)
    # Neither standalone labels nor filing prose may become summary groups.
    nodes.extend(
        [
            observation("Standalone panel", valueString="Suggested group: Lipids"),
            observation("Filing group", comment_note=True, comment="Suggested group: Liver"),
        ]
    )
    direct.extend(["Standalone panel", "Filing group"])
    report, index = report_and_index(nodes, direct)
    report.code = CodeableConcept({"text": "Supplied title"})
    output = await investigation(report, index)
    root = delivered(output, transport)
    caption = root.find("caption")
    assert caption.text == "Issued 20/01/2024 12:00 GMT — Supplied title" + (f" ({expected})" if expected else "")
    assert not list(caption) and not caption.attrib
    assert not root.findall(".//linkHtml")
    assert output.organizer["code"]["originalText"] == "Supplied title"
    assert len(output.organizer["component"]) == len(labels) + 1


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["base64", "mime"])
@pytest.mark.parametrize(
    "collection,received,collected_text,received_text,low,high",
    [
        (
            {"collectedDateTime": "2024-01-20T10:46:00Z"},
            "2024-01-21T10:00:00Z",
            "20/01/2024 10:46 GMT",
            "21/01/2024 10:00 GMT",
            "20240120104600+0000",
            "20240120104600+0000",
        ),
        (
            {"collectedDateTime": "2024-07-20T10:46:00Z"},
            "2024-07-21T10:00:00Z",
            "20/07/2024 11:46 BST",
            "21/07/2024 11:00 BST",
            "20240720104600+0000",
            "20240720104600+0000",
        ),
        (
            {"collectedDateTime": "2024-07-20T10:46:37.125Z"},
            "2024-01-21T10:00:12Z",
            "20/07/2024 11:46:37.125 BST",
            "21/01/2024 10:00:12 GMT",
            "20240720104637.125+0000",
            "20240720104637.125+0000",
        ),
        (
            {"collectedPeriod": {"start": "2024-07-20T10:46:01Z", "end": "2024-07-20T10:46:37.125Z"}},
            "2024-07-20T10:47:00.025Z",
            "20/07/2024 11:46:01 BST – 20/07/2024 11:46:37.125 BST",
            "20/07/2024 11:47:00.025 BST",
            "20240720104601+0000",
            "20240720104637.125+0000",
        ),
        (
            {"collectedPeriod": {"start": "2024-07-20T10:46:00.125Z", "end": "2024-07-20T10:46:00.250Z"}},
            None,
            "20/07/2024 11:46:00.125 BST – 20/07/2024 11:46:00.250 BST",
            "Not supplied",
            "20240720104600.125+0000",
            "20240720104600.250+0000",
        ),
        ({"collectedDateTime": "2024"}, "2024-02", "2024", "02/2024", "2024", "2024"),
        (
            {"collectedPeriod": {"start": "2024-02", "end": "2024-03-04"}},
            None,
            "02/2024 – 04/03/2024",
            "Not supplied",
            "202402",
            "20240304",
        ),
        ({"collectedPeriod": {"start": "2024-02"}}, None, "02/2024 – Unknown end", "Not supplied", "202402", None),
        ({"collectedPeriod": {"end": "2024"}}, None, "Unknown start – 2024", "Not supplied", None, "2024"),
        ({}, None, "Not supplied", "Not supplied", None, None),
    ],
)
async def test_specimen_display_keeps_precision_and_structured_bounds(
    transport, collection, received, collected_text, received_text, low, high
):
    report, index = report_and_index([], [])
    add_specimen(report, index, "sample", collection)
    index["Specimen/sample"].receivedTime = FHIRDate(received) if received else None
    output = await investigation(report, index)
    root = delivered(output, transport)
    cells = root.findall("table/tbody/tr/td")
    assert [cell.text for cell in cells][2:] == [collected_text, received_text]
    # The opt-in specimen precision must not change the default used by headings.
    assert display_time(FHIRDate("2024-07-20T10:46:37.125Z")) == "20/07/2024 11:46 BST"
    assert [col.get("width") for col in root.findall("table/colgroup/col")] == ["30%", "30%", "20%", "20%"]
    structured = ET.fromstring(
        xmltodict.unparse(
            {"organizer": {"@xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance", **output.organizer}}
        )
    )
    for bound, value in (("low", low), ("high", high)):
        node = structured.find(f"effectiveTime/{bound}")
        assert node.get("value") == value
        assert node.get("nullFlavor") == (None if value else "UNK")


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["base64", "mime"])
@pytest.mark.parametrize("newline", ["\r\n", "\r", "\n"])
async def test_every_source_line_survives_only_in_report_level_prose(transport, newline):
    lines = ["", "  A < B & C  ", "Next line", "", "", " \t ", "Last paragraph", "", ""]
    prose = newline.join(lines)
    report, index = report_and_index(
        [
            observation("result", valueString=prose, comment=prose),
            observation("filing", comment_note=True, valueString=prose),
            observation("attached", [("derived-from", "result")], comment_note=True, comment=prose),
        ],
        ["result", "filing", "attached"],
    )
    report.conclusion = prose
    output = await investigation(report, index)
    root = delivered(output, transport)
    paragraphs = root.findall("paragraph")
    # Labels remain separate bold paragraphs; each source field stays in one
    # paragraph with precisely one break per supplied line separator.
    multiline = [p for p in paragraphs if p.find("content/br") is not None]
    assert len(paragraphs) == 5
    assert len(multiline) == 2
    for paragraph in multiline:
        assert len(paragraph.findall("content/br")) == len(lines) - 1
        # Read exact source text from the actual pretty-printed payload, without
        # stripping or using a compact serializer to conceal added whitespace.
        assert [c.text or "" for c in paragraph if c.find("br") is None] == lines
        assert len(paragraph) == 2 * len(lines) - 1
        assert all(paragraph[i].find("br") is not None for i in range(1, len(paragraph), 2))
    assert root.find("table/tbody/tr/td/paragraph") is None
    assert root.find("table//br") is None
    attached = output.table["table"][0]["tbody"]["tr"][-1]["td"]
    assert attached == [{"@colspan": 4, "#text": prose}]
    assert not root.findall(".//BR")
    assert all(p.text or list(p) for p in root.findall(".//paragraph"))
    # Narrative formatting must not normalize structured source strings.
    structured = output.organizer["component"][0]["observation"]
    assert structured["value"]["#text"] == prose
    assert structured["text"] == prose
    xml = xmltodict.unparse({"organizer": output.organizer})
    assert prose.replace("&", "&amp;").replace("<", "&lt;") in xml


@pytest.mark.parametrize("transport", ["base64", "mime"])
@pytest.mark.parametrize(
    "source,lines",
    [
        ("", [""]),
        (" \t ", [" \t "]),
        ("\r\n\r\n", ["", "", ""]),
        ("\rA\r\n\n B \n", ["", "A", "", " B ", ""]),
    ],
)
def test_source_lines_are_not_trimmed_or_reflowed(transport, source, lines):
    paragraphs = narrative_paragraphs(source)
    assert len(paragraphs) == 1
    if len(lines) == 1:
        assert paragraphs == [source]
    else:
        root = delivered(SimpleNamespace(table={"paragraph": paragraphs}), transport)
        paragraph = root.find("paragraph")
        assert len(paragraph.findall("content/br")) == len(lines) - 1
        assert [c.text or "" for c in paragraph if c.find("br") is None] == lines
