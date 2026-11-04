"""Exercise the delivery serializer, not just narrative dictionaries."""

import base64
from xml.etree import ElementTree as ET

import pytest

from app.ccda.convert_mime import base64_xml
from app.ccda.entries.allergy import allergy
from app.tests.test_allergy_entry import get_observation, resource


@pytest.mark.parametrize(
    "notes,lines",
    [
        (
            ["Significance : Minor\nEpisodicity : First\nTest Case 1.1 This is a test"],
            ["Significance : Minor", "Episodicity : First", "Test Case 1.1 This is a test"],
        ),
        (["Significance : Minor\r\nEpisodicity : First"], ["Significance : Minor", "Episodicity : First"]),
        (["Significance : Minor\rEpisodicity : First"], ["Significance : Minor", "Episodicity : First"]),
        (["A\nB", "C\nD"], ["A", "B", "C", "D"]),
        (["Single line"], ["Single line"]),
        (["Line one\n\nLine three"], ["Line one", "", "Line three"]),
        (["A & B <unknown>\n  Keep words  "], ["A & B <unknown>", "  Keep words  "]),
    ],
)
def test_source_annotations_keep_structured_text_but_render_narrative_lines(notes, lines):
    source = resource(note=[{"text": text} for text in notes], onsetDateTime="2020-01-02")
    original = source.as_json()
    result = allergy(source)
    comments = [r["act"]["text"]["xmlText"] for r in get_observation(result)["entryRelationship"]]
    assert comments == notes
    assert source.as_json() == original
    xml = base64.b64decode(
        base64_xml(
            {
                "section": {
                    "@xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
                    "text": {"table": {"tbody": {"tr": {"td": result.row}}}},
                    "entry": result.entry,
                }
            }
        )
    ).decode()
    root = ET.fromstring(xml)
    cells = root.findall("./text/table/tbody/tr/td")
    note_cell = cells[5]
    assert len(note_cell.findall(".//br")) == len(lines)
    assert [node.text or "" for node in note_cell] == [f"{line} " for line in lines]
    assert all(node.tag == "content" and list(node)[0].tag == "br" for node in note_cell)
    assert "<BR>" not in xml and "&lt;br" not in xml
    assert "<br/>" in xml
    # XML parsers normalise CR/CRLF; the model and raw delivery retain the source text.
    for note in notes:
        assert note.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") in xml
    structured = root.findall("./entry/act/entryRelationship/observation/entryRelationship/act/text/xmlText")
    assert len(structured) == len(notes)
    assert all(len(node) == 0 for node in structured)
    assert [node.text for node in structured] == [n.replace("\r\n", "\n").replace("\r", "\n") for n in notes]
    assert [node.text for node in cells[0]] == ["Onset: 02/01/2020 ", "Asserted: 03/02/2024 "]
    assert [node.text for node in cells[2]] == ["Clinical status: Active ", "Verification status: Confirmed "]
    assert len(cells[0].findall(".//br")) == len(cells[2].findall(".//br")) == 2
