"""Clinical-content and relationship regression tests for investigation grouping."""

import json
from pathlib import Path

import pytest
import xmltodict
from fhirclient.models.bundle import Bundle
from fhirclient.models.diagnosticreport import DiagnosticReport
from fhirclient.models.observation import Observation

from app.ccda.entries.investigation_grouping import build_investigation_graph, group_investigation, has_result_value
from app.ccda.entries.results import investigation

FIXTURES = Path(__file__).parent / "fixtures/bundles/investigations"


def observation(name, links=(), comment_note=False, **fields):
    """Build a minimal typed source observation for a clinical scenario."""
    return Observation(
        {
            "resourceType": "Observation",
            "id": name,
            "status": "final",
            "code": {
                "coding": [
                    {
                        "system": "http://snomed.info/sct",
                        "code": "37331000000100" if comment_note else "123",
                        "display": name,
                    }
                ]
            },
            "identifier": [{"system": "https://example.test/observations", "value": name}],
            "related": [{"type": kind, "target": {"reference": f"Observation/{target}"}} for kind, target in links],
            **fields,
        }
    )


def report_and_index(observations, direct):
    """Construct one report and an index that includes reverse-only links."""
    report = DiagnosticReport(
        {
            "resourceType": "DiagnosticReport",
            "id": "report",
            "status": "final",
            "code": {"text": "Laboratory report"},
            "issued": "2024-01-20T12:00:00Z",
            "identifier": [{"system": "https://example.test/reports", "value": "report"}],
            "result": [{"reference": f"Observation/{name}"} for name in direct],
        }
    )
    index = {f"Observation/{o.id}": o for o in observations}
    index["DiagnosticReport/report"] = report
    return report, index


@pytest.mark.parametrize("direction", ["forward", "reverse", "both"])
@pytest.mark.asyncio
async def test_membership_directions_and_reverse_filing_comment(direction):
    panel = observation("panel", [("has-member", "hb")] if direction != "reverse" else [])
    hb = observation(
        "hb", [("derived-from", "panel")] if direction != "forward" else [], valueQuantity={"value": 137, "unit": "g/L"}
    )
    note = observation("note", [("derived-from", "hb")], comment_note=True, comment="Repeat in three months")
    report, index = report_and_index([note, hb, panel], ["panel"])
    output = await investigation(report, index)
    xml = xmltodict.unparse({"table": output.table})
    assert "Test group: panel" in xml
    assert "Test group: hb" not in xml
    assert "Repeat in three months" in xml
    assert len(output.organizer["component"]) == 1


@pytest.mark.asyncio
async def test_flat_string_and_narrative_results_survive():
    report, index = report_and_index(
        [
            observation("panel-label"),
            observation("INR", valueString="Ap 2.400"),
            observation("oestradiol", comment="Result 8.8 in source text"),
        ],
        ["panel-label", "INR", "oestradiol"],
    )
    output = await investigation(report, index)
    xml = xmltodict.unparse({"table": output.table})
    assert "Test group:" not in xml
    assert "Ap 2.400" in xml and "Result 8.8 in source text" in xml
    assert len(output.organizer["component"]) == 3


@pytest.mark.asyncio
async def test_shared_result_once_with_association_at_second_group():
    report, index = report_and_index(
        [
            observation("a", [("has-member", "test")]),
            observation("b", [("has-member", "test")]),
            observation("test", valueQuantity={"value": 0}),
        ],
        ["a", "b"],
    )
    output = await investigation(report, index)
    assert len(output.organizer["component"]) == 1
    assert "Also associated: test" in xmltodict.unparse({"table": output.table})


@pytest.mark.asyncio
async def test_broken_links_and_cycle_preserve_available_content():
    report, index = report_and_index(
        [
            observation("a", [("has-member", "b")], comment="Panel A notes"),
            observation("b", [("has-member", "a"), ("has-member", "missing"), ("has-member", "test")]),
            observation("test", valueString="Detected"),
            # Unrelated damage must not leak into this report's diagnostics.
            observation("unrelated", [("has-member", "absent")]),
        ],
        ["a"],
    )
    grouping = group_investigation(report, index)
    assert len(grouping.issues) == 1
    assert "missing" in grouping.issues[0]
    output = await investigation(report, index)
    xml = xmltodict.unparse({"table": output.table})
    assert "Panel A notes" in xml and "Detected" in xml
    assert "could not be resolved" in xml
    assert len(output.organizer["component"]) == 1


def test_aliases_and_report_boundaries():
    panel = observation("panel", [("has-member", "test"), ("has-member", "elsewhere")])
    test = observation("test", [("derived-from", "panel")])
    report, index = report_and_index([panel, test, observation("elsewhere")], ["panel"])
    other, _ = report_and_index([], ["elsewhere"])
    other.id = "other"
    index["DiagnosticReport/other"] = other
    index["urn:uuid:panel"] = panel
    report.result[0].reference = "urn:uuid:panel"
    grouping = group_investigation(report, index)
    assert len(grouping.observations) == 2
    assert grouping.members["Observation/panel"] == ["Observation/test"]
    assert any("boundary" in issue for issue in grouping.issues)


@pytest.mark.parametrize("value", [{"valueBoolean": False}, {"valueQuantity": {"value": 0}}])
def test_zero_and_false_are_present_values(value):
    assert has_result_value(observation("value", **value))


@pytest.mark.asyncio
@pytest.mark.parametrize("nhs,count", [("9730333939", 20), ("9465700088", 34), ("9692136744", 32)])
@pytest.mark.parametrize("shared_graph", [False, True])
async def test_saved_supplier_reports_convert_and_match_approved_example(nhs, count, shared_graph):
    source = Bundle(json.loads((FIXTURES / f"{nhs}.json").read_text()))
    index = {}
    for entry in source.entry:
        resource = entry.resource
        index[f"{resource.resource_type}/{resource.id}"] = resource
        if entry.fullUrl:
            index[entry.fullUrl] = resource
    reports = [e.resource for e in source.entry if isinstance(e.resource, DiagnosticReport)]
    assert len(reports) == count
    graph = build_investigation_graph(index) if shared_graph else None
    outputs = [await investigation(report, index, graph) for report in reports]
    for output in outputs:
        for table in output.table["table"]:
            width = len(table["thead"]["tr"]["th"])
            for row in table["tbody"]["tr"]:
                assert sum(int(cell.get("@colspan", 1)) if isinstance(cell, dict) else 1 for cell in row["td"]) == width
        xmltodict.parse(xmltodict.unparse({"result": {"organizer": output.organizer, "narrative": output.table}}))
    if nhs == "9730333939":
        approved_path = (
            Path(__file__).parents[2]
            / "docs/assurance/evidence/investigation-grouping-comparison/proposed-grouping.xml"
        )
        approved = xmltodict.parse(approved_path.read_text())["ClinicalDocument"]
        section = next(
            x["section"]
            for x in approved["component"]["structuredBody"]["component"]
            if x["section"]["code"]["@code"] == "30954-2"
        )
        for output, entry, narrative in zip(outputs, section["entry"], section["text"]["list"]["item"], strict=True):
            # Normalise XML whitespace; dictionary output has typed scalar values.
            actual = xmltodict.parse(xmltodict.unparse({"item": output.table}))["item"]
            organizer = xmltodict.parse(xmltodict.unparse({"organizer": output.organizer}))["organizer"]
            # The reviewed grouping stays the same; labels, conclusions and
            # specimen notes are intentional subsequent improvements.
            actual_table = actual["table"][0] if isinstance(actual["table"], list) else actual["table"]
            actual_groups = [r for r in actual_table["tbody"]["tr"] if "Test group:" in str(r)]
            expected_groups = [r for r in narrative["table"]["tbody"]["tr"] if "Test group:" in str(r)]
            assert actual_groups == expected_groups
            assert actual["caption"] == narrative["caption"]
            components = organizer.get("component", [])
            for component in components if isinstance(components, list) else [components]:
                component["observation"].get("code", {}).pop("originalText", None)
            # Status mapping now supersedes the historical prototype too.
            expected = entry["organizer"]
            organizer.pop("statusCode", None)
            expected.pop("statusCode", None)
            for tree in (organizer, expected):
                tree.pop("effectiveTime", None)
                tree.pop("specimen", None)
                children = tree.get("component", [])
                for child in children if isinstance(children, list) else [children]:
                    child["observation"].pop("statusCode", None)
                    child["observation"].pop("effectiveTime", None)
                    child["observation"].pop("value", None)  # Tested by the value-mapping regressions.
                    child["observation"].pop("referenceRange", None)  # Tested by typed range regressions.
            assert organizer == expected


@pytest.mark.asyncio
async def test_nested_group_preserves_own_value_and_comment_only_members_are_not_groups():
    report, index = report_and_index(
        [
            observation("outer", [("has-member", "inner")], comment="Sample haemolysed"),
            observation("inner", [("has-member", "test")], valueString="Panel summary"),
            observation("test", [("has-member", "note")], valueQuantity={"value": 1}),
            observation("note", comment_note=True, comment="Telephone patient"),
        ],
        ["outer"],
    )
    output = await investigation(report, index)
    narrative = xmltodict.unparse({"table": output.table})
    assert "Test group: outer" in narrative and "Test group: inner" in narrative
    assert "Test group: test" not in narrative
    assert "Sample haemolysed" in narrative and "Panel summary" in narrative
    assert "Telephone patient" in narrative
    assert len(output.organizer["component"]) == 2


@pytest.mark.asyncio
async def test_missing_direct_reference_shows_notice_without_losing_valid_result():
    report, index = report_and_index([observation("valid", valueString="Negative")], ["missing", "valid"])
    output = await investigation(report, index)
    narrative = xmltodict.unparse({"table": output.table})
    assert "Negative" in narrative and "could not be resolved" in narrative
    assert len(output.organizer["component"]) == 1


@pytest.mark.asyncio
async def test_shared_graph_keeps_report_boundaries_and_diagnostics_independent():
    report, index = report_and_index(
        [
            observation("a", [("has-member", "shared"), ("has-member", "b"), ("has-member", "missing")]),
            observation("b", [("has-member", "shared")]),
            observation("shared", valueString="Detected"),
        ],
        ["a", "shared", "absent"],
    )
    other, _ = report_and_index([], ["b", "shared"])
    other.id = "other"
    index["DiagnosticReport/other"] = other
    index["urn:uuid:other"] = other
    index["urn:uuid:a"] = index["Observation/a"]
    report.result[0].reference = "urn:uuid:a"
    graph = build_investigation_graph(index)
    assert len(graph.reports) == 2
    assert graph.aliases["urn:uuid:a"] == "Observation/a"
    assert graph.root_owners["Observation/shared"] == frozenset({id(report), id(other)})
    with pytest.raises(TypeError):
        graph.observations["new"] = index["Observation/a"]

    first = group_investigation(report, graph)
    assert list(first.observations) == ["Observation/a", "Observation/shared"]
    assert first.issues == [
        "Missing report result: Observation/absent",
        "Missing linked observation: Observation/a -> Observation/missing",
        "Link crosses report boundary: Observation/a -> Observation/b",
    ]
    first.issues.append("A renderer's local diagnostic")
    first.direct.clear()
    first.members.clear()
    again = group_investigation(report, graph)
    second = group_investigation(other, graph)
    assert again == group_investigation(report, index)
    assert list(second.observations) == ["Observation/b", "Observation/shared"]
    assert second.issues == []
    for current in (report, other, report):
        assert await investigation(current, index, graph) == await investigation(current, index)


def test_shared_graph_preserves_edge_discovery_order_and_distinct_url_resources():
    # An early edge only becomes reachable on the second pass. A plain BFS
    # would discover c before d, changing existing discovery order.
    report, index = report_and_index(
        [
            observation("b", [("has-member", "c")]),
            observation("a", [("has-member", "b"), ("has-member", "d")]),
            observation("c"),
            observation("d"),
        ],
        ["a"],
    )
    foreign = observation("c", valueString="Different resource with the same id")
    index["https://other.example/Observation/c"] = foreign
    graph = build_investigation_graph(index)
    grouping = group_investigation(report, graph)
    assert list(grouping.observations) == ["Observation/a", "Observation/b", "Observation/d", "Observation/c"]
    assert graph.observations["https://other.example/Observation/c"] is foreign
    assert "https://other.example/Observation/c" not in grouping.observations


def test_shared_graph_supports_report_outside_resource_index():
    report, index = report_and_index([observation("value", valueBoolean=False)], ["value", "missing"])
    del index["DiagnosticReport/report"]
    grouping = group_investigation(report, build_investigation_graph(index))
    assert grouping.direct == ["Observation/value"]
    assert grouping.issues == ["Missing report result: Observation/missing"]


@pytest.mark.asyncio
@pytest.mark.parametrize("sections", [0, 1, 2])
@pytest.mark.parametrize("empty", [False, True])
async def test_conversion_builds_graph_once_for_all_nonempty_investigation_sections(monkeypatch, sections, empty):
    from unittest.mock import Mock

    from app.ccda import fhir2ccda

    raw = json.loads((FIXTURES / "9730333939.json").read_text())
    investigation_list = next(
        entry for entry in raw["entry"] if entry["resource"].get("title") == "Investigations and results"
    )
    raw["entry"].remove(investigation_list)
    for number in range(sections):
        section = json.loads(json.dumps(investigation_list))
        section["resource"]["id"] = f"investigations-{number}"
        section.pop("fullUrl", None)
        if empty:
            section["resource"]["entry"] = []
        raw["entry"].append(section)
    source = Bundle(raw)
    index = {}
    for entry in source.entry:
        index[f"{entry.resource.resource_type}/{entry.resource.id}"] = entry.resource
        if entry.fullUrl:
            index[entry.fullUrl] = entry.resource
    builder = Mock(wraps=build_investigation_graph)
    monkeypatch.setattr(fhir2ccda, "build_investigation_graph", builder)
    result = await fhir2ccda.convert_bundle(source, index)
    assert builder.call_count == (1 if sections and not empty else 0)
    rendered = [
        component["section"]
        for component in result["ClinicalDocument"]["component"]["structuredBody"]["component"]
        if component["section"]["code"]["@code"] == "30954-2"
    ]
    assert len(rendered) == sections
    if sections == 2:
        assert rendered[0] == rendered[1]


def test_indexed_child_entry_resolves_parent_siblings_and_display_roots():
    report, index = report_and_index(
        [
            observation("a", [("derived-from", "panel")]),
            observation("b", [("derived-from", "panel")]),
            observation("panel", [("has-member", "a")]),
            observation("note", [("derived-from", "a")], comment_note=True),
            observation("report-note", comment_note=True),
        ],
        ["a", "report-note"],
    )
    graph = build_investigation_graph(index)
    grouping = group_investigation(report, graph)
    assert grouping.roots == ("Observation/panel",)
    assert grouping.report_comments == ("Observation/report-note",)
    assert grouping.members == {"Observation/panel": ["Observation/a", "Observation/b"]}
    assert grouping.comments == {"Observation/a": ["Observation/note"]}
    # Reciprocal source links are normalised and deduplicated during the build.
    assert [a.child for a in graph.associations["Observation/panel"]] == ["Observation/a", "Observation/b"]
    with pytest.raises(TypeError):
        graph.associations["new"] = ()


def test_indexed_resolution_does_not_scan_unrelated_edges():
    from dataclasses import replace

    class IndexedEdges(tuple):
        reads = 0

        def __iter__(self):
            raise AssertionError("Report resolution must not scan all bundle edges")

        def __getitem__(self, position):
            self.reads += 1
            return super().__getitem__(position)

    report, index = report_and_index(
        [observation("panel", [("has-member", "result")]), observation("result")]
        + [observation(f"unrelated-{i}", [("has-member", f"unrelated-{i}")]) for i in range(1000)],
        ["panel"],
    )
    graph = build_investigation_graph(index)
    edges = IndexedEdges(graph.edges)
    grouping = group_investigation(report, replace(graph, edges=edges))
    assert grouping.roots == ("Observation/panel",)
    assert list(grouping.observations) == ["Observation/panel", "Observation/result"]
    assert edges.reads == 2  # Discovery and local diagnostics, regardless of bundle size.


def test_indexed_discovery_preserves_scan_pass_order_with_multiple_seeds():
    report, index = report_and_index(
        [
            observation("b", [("has-member", "c")]),
            observation("e", [("has-member", "f")]),
            observation("a", [("has-member", "b")]),
            observation("d", [("has-member", "e")]),
            observation("c"),
            observation("f"),
        ],
        ["d", "a"],
    )
    grouping = group_investigation(report, build_investigation_graph(index))
    assert list(grouping.observations) == [f"Observation/{name}" for name in ("d", "a", "b", "e", "c", "f")]
    assert grouping.roots == ("Observation/d", "Observation/a")
    assert list(grouping.members) == [f"Observation/{name}" for name in ("b", "e", "a", "d")]


def test_indexed_resolution_handles_deep_reverse_ordered_chain():
    length = 1500
    nodes = [observation(str(i), [("has-member", str(i + 1))]) for i in reversed(range(length))]
    report, index = report_and_index([observation(str(length)), *nodes], ["0"])
    grouping = group_investigation(report, build_investigation_graph(index))
    assert len(grouping.observations) == length + 1
    assert grouping.roots == ("Observation/0",)
    assert list(grouping.observations) == [f"Observation/{i}" for i in range(length + 1)]
