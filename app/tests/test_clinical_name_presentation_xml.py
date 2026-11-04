"""Delivery XML checks for allergy, problem and immunization narrative names."""

import base64
import re
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from fhirclient.models.condition import Condition
from fhirclient.models.immunization import Immunization

from app.ccda.convert_mime import base64_xml
from app.ccda.entries.allergy import allergy
from app.ccda.entries.immunization_entry import immunization_entry
from app.ccda.entries.problem import problem
from app.tests.test_allergy_entry import resource

FIXTURES = Path(__file__).parent / "fixtures"


def clinical_entry(kind, notes=True):
    if kind == "allergy":
        return allergy(
            resource(
                id="allergy-source",
                onsetDateTime="2020-01-02",
                criticality="high",
                note=[{"text": "First & <unknown>\n\nLast\n"}] if notes else None,
                reaction=[
                    {
                        "severity": "severe",
                        "manifestation": [{"coding": [{"code": "1", "display": "Rash"}]}],
                    }
                ],
            )
        )
    if kind == "problem":
        return problem(
            Condition(
                {
                    "resourceType": "Condition",
                    "id": "problem-source",
                    "subject": {"reference": "Patient/test"},
                    "clinicalStatus": "active",
                    "assertedDate": "2024-02-03",
                    "code": {"coding": [{"code": "123", "display": "Problem & <name>"}]},
                }
            )
        )
    return immunization_entry(
        Immunization(
            {
                "resourceType": "Immunization",
                "id": "immunization-source",
                "patient": {"reference": "Patient/test"},
                "status": "completed",
                "notGiven": False,
                "primarySource": True,
                "date": "2024-02-03",
                "vaccineCode": {"coding": [{"code": "123", "display": "Vaccine & <name>"}]},
                "lotNumber": "Lot 123",
                **(
                    {
                        "note": [{"text": "First & <unknown>"}, {"text": "Second note"}],
                        "explanation": {
                            "reason": [{"text": "Routine"}],
                            "reasonNotGiven": [{"coding": [{"code": "1", "display": "Deferred"}]}],
                        },
                    }
                    if notes
                    else {}
                ),
            }
        ),
        {},
    )


def delivery(result):
    return ET.fromstring(
        base64.b64decode(
            base64_xml(
                {
                    "section": {
                        "@xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
                        "text": {"table": {"tbody": {"tr": {"td": result.row}}}},
                        "entry": result.entry,
                    }
                }
            )
        )
    )


def structured_xml(root):
    # Baselines were captured before the presentation edit. Only generated UUID
    # roots vary between conversions; retain all source IDs and other attributes.
    entry = root.find("entry")
    for node in entry.iter():
        if re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", node.get("root", "")):
            node.set("root", "GENERATED-UUID")
    return ET.tostring(entry, encoding="unicode").strip()


@pytest.mark.parametrize(
    "kind,column,name,metadata",
    [
        ("allergy", 1, "Test substance", {3: "Rash", 4: "Severe"}),
        ("problem", 2, "Problem & <name>", {0: "03/02/2024", 1: "active"}),
        ("immunization", 1, "Vaccine & <name>", {0: "03/02/2024", 2: "Lot 123", 3: "completed"}),
    ],
)
def test_only_primary_name_is_bold_and_structured_xml_matches_baseline(kind, column, name, metadata):
    root = delivery(clinical_entry(kind))
    cells = root.findall("./text/table/tbody/tr/td")
    anchor = cells[column].find("content")
    assert anchor is not None
    assert anchor.attrib == {"styleCode": "Bold"}
    assert anchor.text == name
    assert list(anchor) == []
    assert root.findall(".//*[@styleCode]") == [anchor]
    for index, value in metadata.items():
        assert cells[index].text == value
    assert structured_xml(root) == (FIXTURES / f"{kind}_presentation_entry.xml.txt").read_text().strip()

    if kind == "allergy":
        assert [node.text for node in cells[0]] == ["Onset: 02/01/2020 ", "Asserted: 03/02/2024 "]
        assert [node.text for node in cells[2]] == [
            "Clinical status: Active ",
            "Verification status: Confirmed ",
            "Criticality: High ",
        ]
        assert [node.text for node in cells[5]] == ["First & <unknown> ", " ", "Last "]
        assert len(cells[5].findall(".//br")) == 3
    elif kind == "immunization":
        contents = cells[column].findall("content")
        assert len(contents) == 2
        assert contents[1].attrib == {}
        breaks = contents[1].findall("br")
        assert [node.tail for node in breaks] == [
            "Notes: First & <unknown>",
            "Second note",
            "Reason: Routine",
            "Reason not given: Deferred",
        ]
        structured_note = root.find("./entry/entryRelationship/act/text/xmlText")
        assert structured_note.text == "First & <unknown>\nSecond note\nReason: Routine\nReason not given: Deferred"
        assert list(structured_note) == []


@pytest.mark.parametrize("kind,column", [("allergy", 1), ("immunization", 1)])
def test_no_notes_adds_no_empty_content_sibling(kind, column):
    root = delivery(clinical_entry(kind, notes=False))
    cells = root.findall("./text/table/tbody/tr/td")
    assert len(cells[column]) == 1
    assert cells[column][0].get("styleCode") == "Bold"
    if kind == "allergy":
        assert not list(cells[5])
        assert cells[5].text is None
