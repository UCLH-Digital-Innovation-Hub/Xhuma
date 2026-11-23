"""One stable structured-time order drives the TOC, report bodies and organizers."""

import copy

import pytest
from defusedxml import ElementTree as ET
from fhirclient.models.bundle import Bundle
from fhirclient.models.fhirdate import FHIRDate

from app.ccda.entries.result_presentation import report_heading_time, report_sort_key
from app.ccda.fhir2ccda import convert_bundle
from app.tests.test_ccda_names import create_bundle_dict
from app.tests.test_investigation_grouping import observation, report_and_index
from app.tests.test_investigation_presentation_xml import NS, serialized_xml
from app.tests.test_result_context import add_specimen


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["base64", "mime"])
async def test_newest_first_collection_fallback_stable_ties_and_unknowns(transport):
    data = create_bundle_dict([{"family": "Test", "given": ["Patient"]}])
    source_order = [
        "unknown",
        "old_collection",
        "tie_first",
        "new_collection",
        "issued",
        "tie_second",
        "ambiguous",
        "unresolved",
        "unknown_last",
    ]
    for name in source_order:
        report, index = report_and_index([observation(name, valueString=f"Body {name}")], [name])
        report.id = name
        report.identifier[0].value = name
        report.code.text = name
        report.issued = None
        if name == "old_collection":
            # A newer issued date must not override trusted collection provenance.
            report.issued = FHIRDate("2026-01-01T00:00:00Z")
            add_specimen(report, index, name, {"collectedDateTime": "2020-01-01T00:00:00Z"})
        elif name == "new_collection":
            report.issued = FHIRDate("2010-01-01T00:00:00Z")
            add_specimen(report, index, name, {"collectedDateTime": "2025-07-01T12:00:00+01:00"})
        elif name.startswith("tie"):
            report.issued = FHIRDate("2024-07-01T12:00:00+01:00" if name == "tie_first" else "2024-07-01T11:00:00Z")
        elif name == "issued":
            report.issued = FHIRDate("2025-01-01T00:00:00Z")
        elif name == "ambiguous":
            report.issued = FHIRDate("2023-01-01T00:00:00Z")
            add_specimen(report, index, "a", {"collectedDateTime": "2027-01-01T00:00:00Z"})
            add_specimen(report, index, "b", {"collectedDateTime": "2028-01-01T00:00:00Z"})
        elif name == "unresolved":
            report.issued = FHIRDate("2022-01-01T00:00:00Z")
            add_specimen(report, index, name, {"collectedDateTime": "2029-01-01T00:00:00Z"})
            index.pop(f"Specimen/{name}")
        data["entry"].append({"resource": report.as_json()})
        data["entry"].extend({"resource": r.as_json()} for r in index.values() if r.resource_type != "DiagnosticReport")
    data["entry"].append(
        {
            "resource": {
                "resourceType": "List",
                "id": "reports",
                "status": "current",
                "mode": "snapshot",
                "title": "Investigations and results",
                "entry": [{"item": {"reference": f"DiagnosticReport/{name}"}} for name in source_order],
            }
        }
    )
    bundle = Bundle(data)
    index = {f"{e.resource.resource_type}/{e.resource.id}": e.resource for e in bundle.entry}
    original = copy.deepcopy(bundle.as_json())
    root = ET.fromstring(serialized_xml(await convert_bundle(bundle, index), transport))
    section = next(
        s
        for s in root.findall(".//c:section", NS)
        if s.findtext("c:title", namespaces=NS) == "Investigations and results"
    )
    toc = section.find("c:text/c:list", NS)
    assert toc.attrib == {"styleCode": "TOC"}
    expected = [
        "new_collection",
        "issued",
        "tie_first",
        "tie_second",
        "ambiguous",
        "unresolved",
        "old_collection",
        "unknown",
        "unknown_last",
    ]
    entries = section.findall("c:entry", NS)
    items = toc.findall("c:item", NS)
    assert len(entries) == len(items) == len(source_order)
    assert [e.find("c:organizer/c:id", NS).get("extension") for e in entries] == expected
    for item, name in zip(items, expected, strict=True):
        assert item.findtext("c:caption", namespaces=NS).endswith(f" — {name}")
        assert f"Body {name}" in "".join(item.itertext())
    assert "Issued" not in items[0].findtext("c:caption", namespaces=NS)
    assert items[1].findtext("c:caption", namespaces=NS).startswith("01/01/2025 00:00 GMT")
    assert all("ID" not in element.attrib for element in toc.iter())
    assert not toc.findall(".//c:linkHtml", NS)
    assert bundle.as_json() == original


@pytest.mark.parametrize(
    "collection,issued,expected_heading,expected_key",
    [
        (
            {"collectedPeriod": {"start": "2024-07-01T12:00:00+01:00", "end": "2024-07-01T16:00:00+01:00"}},
            "2025-01-01T00:00:00Z",
            "Collected 01/07/2024 12:00 BST – 01/07/2024 16:00 BST",
            "2024-07-01T11:00:00+00:00",
        ),
        (
            {"collectedPeriod": {"start": "2024-07-01"}},
            "2025-01-01T00:00:00Z",
            "01/01/2025 00:00 GMT",
            "2025-01-01T00:00:00+00:00",
        ),
        ({"collectedDateTime": "2024"}, None, "2024", "2024-01-01T00:00:00+00:00"),
        ({"collectedDateTime": "2024-02"}, None, "02/2024", "2024-02-01T00:00:00+00:00"),
        ({"collectedDateTime": "2024-07-01"}, None, "01/07/2024", "2024-06-30T23:00:00+00:00"),
    ],
)
def test_sort_key_shares_heading_provenance_without_changing_precision(
    collection, issued, expected_heading, expected_key
):
    report, index = report_and_index([], [])
    report.issued = FHIRDate(issued) if issued else None
    add_specimen(report, index, "sample", collection)
    assert report_heading_time(report, index) == expected_heading
    known, key = report_sort_key(report, index)
    assert known and key.isoformat() == expected_key
