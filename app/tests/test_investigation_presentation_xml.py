"""Wire-level presentation contracts; no receiver-specific anchors are invented."""

import base64
import hashlib
import json
from email import message_from_string

import pytest
import xmltodict
from defusedxml import ElementTree as ET
from fhirclient.models.bundle import Bundle
from fhirclient.models.diagnosticreport import DiagnosticReport

from app.ccda.convert_mime import base64_xml, convert_mime
from app.ccda.entries.results import investigation
from app.ccda.fhir2ccda import convert_bundle
from app.tests.test_ccda_names import create_bundle_dict
from app.tests.test_investigation_grouping import FIXTURES, observation, report_and_index

NS = {"c": "urn:hl7-org:v3"}


def serialized_xml(document, transport):
    if transport == "base64":
        return base64.b64decode(base64_xml(document)).decode()
    return message_from_string(convert_mime(document)).get_payload(0).get_payload()


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["base64", "mime"])
@pytest.mark.parametrize("count", [1, 40])
async def test_serialized_toc_preserves_report_count_duplicates_and_stable_time_order(transport, count):
    data = create_bundle_dict([{"family": "Test", "given": ["Patient"]}])
    # Two distinct reports deliberately have identical captions. Repeated list
    # references must also survive; captions are not unique navigation keys.
    reports = []
    for number, issued in enumerate(("2024-01-20T12:00:00Z", "2023-01-20T12:00:00Z", "2024-01-20T12:00:00Z")):
        report, _ = report_and_index([], [])
        raw = report.as_json()
        raw.update(
            id=f"r{number}",
            code={"text": "Full blood count" if number == 1 else "Diagnostic studies report"},
            issued=issued,
        )
        raw["identifier"][0]["value"] = f"r{number}"
        reports.append(raw)
        data["entry"].append({"resource": raw})
    order = [2, 1, 0, 2] * 10 if count == 40 else [0]
    data["entry"].append(
        {
            "resource": {
                "resourceType": "List",
                "id": "investigations",
                "status": "current",
                "mode": "snapshot",
                "title": "Investigations and results",
                "note": [{"text": "Source completeness warning"}],
                "entry": [{"item": {"reference": f"DiagnosticReport/r{i}"}} for i in order],
            }
        }
    )
    bundle = Bundle(data)
    index = {f"{e.resource.resource_type}/{e.resource.id}": e.resource for e in bundle.entry}
    document = await convert_bundle(bundle, index)
    root = ET.fromstring(serialized_xml(document, transport))
    section = next(s for s in root.findall(".//c:section", NS) if s.find("c:code", NS).get("code") == "30954-2")
    narrative = section.find("c:text", NS)
    toc = narrative.find("c:list", NS)
    assert toc.attrib == {"styleCode": "TOC"}
    assert narrative[0].tag == "{urn:hl7-org:v3}paragraph"
    assert narrative[0].text == "Source completeness warning"
    items = toc.findall("c:item", NS)
    entries = section.findall("c:entry", NS)
    assert len(items) == len(entries) == count
    assert [p.text for p in narrative.findall("c:paragraph", NS)] == [
        "Source completeness warning",
        f"{count} investigation report{'s' if count != 1 else ''}",
        "Select a report below to jump to its results.",
    ]
    order = [i for i in order if i != 1] + [i for i in order if i == 1]
    expected = [
        f"20/01/{'2023' if i == 1 else '2024'} 12:00 GMT — "
        + ("Full blood count" if i == 1 else "Diagnostic studies report")
        for i in order
    ]
    assert not any(word in caption for caption in expected for word in ("ABNORMAL", "NORMAL", "HIGH", "LOW"))
    assert [item.find("c:caption", NS).text for item in items] == expected
    assert [entry.find("c:organizer/c:id", NS).get("extension") for entry in entries] == [f"r{i}" for i in order]
    for item in items:
        assert item[0].tag == "{urn:hl7-org:v3}caption"
        assert item.find("c:caption", NS).attrib == {}
    assert not narrative.findall(".//c:linkHtml", NS)
    assert all("ID" not in element.attrib for element in narrative.iter())
    if count > 1:
        assert expected.count(expected[0]) == 30


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["base64", "mime"])
async def test_serialized_filing_is_plain_prose_under_bold_labels_and_widths(transport):
    report, index = report_and_index(
        [
            observation(
                "result",
                valueQuantity={"value": 10, "unit": "mg/L"},
                referenceRange=[{"high": {"value": 5, "unit": "mg/L"}}],
            ),
            observation("first", comment_note=True, valueString="Normal: source prose", comment="A < B & C\nNext line"),
            observation("second", comment_note=True, comment="Normal: source prose"),
        ],
        ["result", "first", "second"],
    )
    report.conclusion = "Interpretation: source prose"
    output = await investigation(report, index)
    root = ET.fromstring(serialized_xml({"item": output.table}, transport))
    assert root.find("list") is None
    assert [
        p.find("content").text for p in root.findall("paragraph") if p.find("content[@styleCode='Bold']") is not None
    ] == [
        "Report Interpretation",
        "Report-level filing",
        "first",
        "second",
    ]
    assert [p.text for p in root.findall("paragraph") if p.find("content") is None] == [
        "Interpretation: source prose",
        "Normal: source prose",
        "Normal: source prose",
    ]
    multiline = root.findall("paragraph")[5]
    assert [node.text for node in multiline if node.find("br") is None] == ["A < B & C", "Next line"]
    assert len(multiline.findall("content/br")) == 1
    assert multiline.findall(".//*[@styleCode]") == []
    assert [c.get("width") for c in root.findall("table/colgroup/col")] == ["28%", "17%", "20%", "35%"]
    assert root.find("table/tbody/tr/td/content").attrib == {"styleCode": "flagData"}
    assert root.find("table/tbody/tr/td/content").text == "10 mg/L"
    assert [element.tag for element in root] == ["caption", *(["paragraph"] * 8), "table"]


@pytest.mark.asyncio
@pytest.mark.parametrize("value,abnormal", [(4, False), (5, False), (10, True)])
async def test_delivery_only_flags_out_of_range_values(value, abnormal):
    report, index = report_and_index(
        [
            observation(
                "result",
                valueQuantity={"value": value, "unit": "mg/L"},
                referenceRange=[{"high": {"value": 5, "unit": "mg/L"}}],
            )
        ],
        ["result"],
    )
    output = await investigation(report, index)
    root = ET.fromstring(serialized_xml({"item": output.table}, "base64"))
    flags = root.findall(".//*[@styleCode='flagData']")
    assert len(flags) == int(abnormal)
    cell = root.findall("table/tbody/tr/td")[1]
    assert "".join(cell.itertext()).strip() == f"{value} mg/L"
    if abnormal:
        assert flags == [cell.find("content")]
    else:
        assert cell.attrib == {} and len(cell) == 0


# Includes report-level Epic categories with stable IDs and source statuses. Hash
# the complete serialized organizers, including order, identifiers, values,
# ranges, times, codes, statuses, specimens and category observations.
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "nhs,count,digest",
    [
        ("9730333939", 20, "44df8ca5ee6ed0f7c45c7b04487303349daa9f1806b119c4d49de0c970e8f9db"),
        ("9465700088", 34, "c5e56c1551765b14fdf27bc7235c459a383d0dbfb0314e6fa483c7815ca57a00"),
        ("9692136744", 32, "6bb56f08ffe228d44fed55e9c1f42f98f52b54c05e9a7cfeb73a6b072208dd58"),
    ],
)
async def test_supplier_structured_xml_matches_reviewed_snapshot(nhs, count, digest):
    bundle = Bundle(json.loads((FIXTURES / f"{nhs}.json").read_text()))
    index = {f"{e.resource.resource_type}/{e.resource.id}": e.resource for e in bundle.entry}
    outputs = [await investigation(e.resource, index) for e in bundle.entry if isinstance(e.resource, DiagnosticReport)]
    assert len(outputs) == count
    xml = xmltodict.unparse({"entries": {"organizer": [output.organizer for output in outputs]}})
    assert hashlib.sha256(xml.encode()).hexdigest() == digest
