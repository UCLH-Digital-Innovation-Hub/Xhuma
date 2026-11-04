"""Report identity, display timestamps and optional source metadata in lab narrative."""

import pytest
import xmltodict
from fhirclient.models.codeableconcept import CodeableConcept
from fhirclient.models.diagnosticreport import DiagnosticReportPerformer
from fhirclient.models.fhirdate import FHIRDate
from fhirclient.models.fhirreference import FHIRReference
from fhirclient.models.organization import Organization
from fhirclient.models.practitioner import Practitioner

from app.ccda.entries.result_presentation import display_time, report_heading_time
from app.ccda.entries.results import investigation
from app.tests.test_investigation_grouping import observation, report_and_index
from app.tests.test_result_context import add_specimen


@pytest.mark.parametrize(
    "source,expected",
    [
        ("2024-01-20T10:46:00Z", "20/01/2024 10:46 GMT"),
        ("2024-07-20T10:46:00Z", "20/07/2024 11:46 BST"),
        ("2024-10-27T00:30:00Z", "27/10/2024 01:30 BST"),
        ("2024-10-27T01:30:00Z", "27/10/2024 01:30 GMT"),
        ("2024-02-03", "03/02/2024"),
        ("2024-02", "02/2024"),
        ("2024", "2024"),
    ],
)
def test_display_time_preserves_date_precision_and_handles_bst(source, expected):
    assert display_time(FHIRDate(source)) == expected


@pytest.mark.asyncio
async def test_reverse_only_panel_drives_heading_battery_and_code():
    panel = observation(
        "panel",
        code={
            "text": "Full blood count",
            "coding": [{"system": "http://snomed.info/sct", "code": "123", "display": "Coded panel"}],
        },
    )
    report, index = report_and_index(
        [panel, observation("hb", [("derived-from", "panel")], valueQuantity={"value": 130, "unit": "g/L"})], ["hb"]
    )
    add_specimen(report, index, "serum", {"collectedDateTime": "2024-07-20T10:46:00Z"})
    output = await investigation(report, index)
    assert output.table["caption"] == "Full blood count (20/07/2024 11:46 BST)"
    assert output.organizer["@classCode"] == "BATTERY"
    assert output.organizer["code"]["@code"] == "123"
    assert output.organizer["code"]["originalText"] == "Full blood count"
    # Presentation timezone does not alter structured source timestamps.
    assert output.organizer["effectiveTime"]["low"]["@value"] == "20240720104600+0000"
    assert output.organizer["component"][0]["observation"]["effectiveTime"]["low"]["@value"] == "20240120120000+0000"
    # Specimen appears even without source notes.
    assert output.table["table"][1]["tbody"]["tr"][0]["td"][0] == "Serum — serum"
    for table in output.table["table"]:
        assert len(table["colgroup"]["col"]) == len(table["thead"]["tr"]["th"])
        assert sum(int(c["@width"].rstrip("%")) for c in table["colgroup"]["col"]) == 100
    xml = xmltodict.unparse({"report": output.table})
    assert xml.index("<colgroup>") < xml.index("<thead>")


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["flat", "mixed", "cycle", "unplaced", "missing"])
async def test_only_a_single_resolved_panel_is_a_battery(case):
    nodes = [observation("panel", [("has-member", "result")]), observation("result", valueString="Found")]
    direct = ["panel"]
    if case == "flat":
        nodes[0].related = []
        direct.append("result")
    elif case == "mixed":
        nodes.append(observation("separate", valueString="Separate"))
        direct.append("separate")
    elif case == "cycle":
        nodes[1] = observation("result", [("has-member", "panel")], valueString="Found")
    elif case == "unplaced":
        nodes.append(observation("loop", [("has-member", "loop")], valueString="Loop"))
        direct.append("loop")
    else:
        direct.append("missing")
    report, index = report_and_index(nodes, direct)
    report.code = CodeableConcept({"text": "Supplied report title"})
    output = await investigation(report, index)
    assert output.organizer["@classCode"] == "CLUSTER"
    assert output.table["caption"] == "Supplied report title (Issued 20/01/2024 12:00 GMT)"
    assert all(
        t.get("caption") not in ("Specimen notes", "Performing laboratory / organisation")
        for t in output.table["table"]
    )  # No invented sample/laboratory rows.


def test_caption_handles_collection_period_conflicts_missing_references_and_issue_time():
    report, index = report_and_index([], [])
    add_specimen(report, index, "a", {"collectedPeriod": {"start": "2024-02-03", "end": "2024-02-04"}})
    assert report_heading_time(report, index) == "Collected 03/02/2024 – 04/02/2024"
    index["urn:uuid:a"] = index["Specimen/a"]
    report.specimen.append(FHIRReference({"reference": "urn:uuid:a"}))
    assert report_heading_time(report, index) == "Collected 03/02/2024 – 04/02/2024"
    add_specimen(report, index, "b", {"collectedDateTime": "2024-02-05"})
    assert report_heading_time(report, index) == "Issued 20/01/2024 12:00 GMT"
    report.specimen.pop()
    report.specimen.append(FHIRReference({"reference": "Specimen/missing"}))
    assert report_heading_time(report, index) == "Issued 20/01/2024 12:00 GMT"
    report.issued = None
    assert report_heading_time(report, index) == "Time not supplied"


def test_incomplete_collection_period_does_not_invent_caption_date():
    report, index = report_and_index([], [])
    add_specimen(report, index, "a", {"collectedPeriod": {"start": "2024-02"}})
    assert report_heading_time(report, index) == "Issued 20/01/2024 12:00 GMT"


@pytest.mark.asyncio
async def test_laboratory_rows_keep_performer_scope_deduplicate_aliases_and_escape_text():
    result = observation(
        "Haemoglobin",
        valueString="Found",
        performer=[{"reference": "urn:uuid:lab"}, {"reference": "Organization/other"}],
    )
    report, index = report_and_index([result], ["Haemoglobin"])
    lab = Organization(
        {
            "resourceType": "Organization",
            "id": "lab",
            "name": "A & B Lab",
            "address": [{"line": ["1 Main Street"], "city": "Cambridge", "postalCode": "CB1"}],
            "telecom": [{"system": "phone", "value": "01234"}, {"system": "email", "value": "lab@example.test"}],
        }
    )
    index.update(
        {
            "Organization/lab": lab,
            "urn:uuid:lab": lab,
            "Organization/other": Organization({"resourceType": "Organization", "id": "other", "name": "Other lab"}),
            "Organization/unrelated": Organization(
                {"resourceType": "Organization", "id": "unrelated", "name": "Unrelated lab"}
            ),
            "Practitioner/p": Practitioner({"resourceType": "Practitioner", "id": "p"}),
        }
    )
    report.performer = [
        DiagnosticReportPerformer({"actor": {"reference": ref}})
        for ref in ("Organization/lab", "urn:uuid:lab", "Practitioner/p", "Organization/missing")
    ]
    output = await investigation(report, index)
    table = output.table["table"][1]
    assert table["caption"] == "Performing laboratory / organisation"
    rows = table["tbody"]["tr"]
    assert len(rows) == 2
    assert rows[0]["td"][:2] == ["A & B Lab", "Report; Haemoglobin"]
    assert rows[1]["td"][:2] == ["Other lab", "Haemoglobin"]
    xml = xmltodict.unparse({"table": table})
    assert "A &amp; B Lab" in xml and "1 Main Street" in xml and "phone: 01234" in xml
    assert "Unrelated lab" not in xml
    assert [c["@width"] for c in table["colgroup"]["col"]] == ["25%", "25%", "30%", "20%"]


@pytest.mark.asyncio
async def test_separate_group_tables_keep_members_comments_and_shared_associations():
    report, index = report_and_index(
        [
            observation("standalone", valueString="Independent"),
            observation("Blood count", [("has-member", "Haemoglobin"), ("has-member", "shared")]),
            observation("Renal function", [("has-member", "Sodium"), ("has-member", "shared")]),
            observation("Haemoglobin", valueQuantity={"value": 130, "unit": "g/L"}),
            observation("Sodium", valueQuantity={"value": 140, "unit": "mmol/L"}),
            observation("shared", valueQuantity={"value": 0}),
            observation("panel-note", [("derived-from", "Blood count")], comment_note=True, comment="Panel comment"),
            observation("result-note", [("derived-from", "Sodium")], comment_note=True, comment="Result comment"),
            observation("filing", comment_note=True, comment="Report comment"),
        ],
        ["standalone", "Blood count", "Renal function", "filing"],
    )
    output = await investigation(report, index)
    tables = output.table["table"]
    assert [t["caption"]["#text"] for t in tables] == [
        "Blood count",
        "Renal function",
        "Other results",
    ]
    texts = [xmltodict.unparse({"table": t}) for t in tables]
    assert "Haemoglobin" in texts[0] and "Panel comment" in texts[0] and "Sodium" not in texts[0]
    assert "Sodium" in texts[1] and "Result comment" in texts[1] and "Panel comment" not in texts[1]
    assert "Also associated: shared" in texts[1]
    assert "Independent" in texts[2]
    assert "Report comment" in xmltodict.unparse({"list": output.table["list"]})
    assert all("Report comment" not in text for text in texts)
    # Tables reorder presentation only: structured results retain source traversal
    # order and the shared result has exactly one structured observation.
    components = output.organizer["component"]
    assert [c["observation"]["code"]["@displayName"] for c in components] == [
        "standalone",
        "Haemoglobin",
        "shared",
        "Sodium",
    ]
    for table in tables:
        assert table["caption"]["@styleCode"] == "Bold"
        assert table["thead"]["tr"]["th"] == ["Component", "Value", "Reference Range", "Comments"]
        assert [c["@width"] for c in table["colgroup"]["col"]] == ["30%", "20%", "20%", "30%"]
        for row in table["tbody"]["tr"]:
            assert sum(int(c.get("@colspan", 1)) if isinstance(c, dict) else 1 for c in row["td"]) == 4


@pytest.mark.asyncio
async def test_nested_group_headings_are_bold_and_indented_without_repeating_outer_caption():
    report, index = report_and_index(
        [
            observation("outer", [("has-member", "inner")]),
            observation("inner", [("has-member", "deeper")], valueString="Panel summary"),
            observation("deeper", [("has-member", "result")]),
            observation("result", valueBoolean=False),
        ],
        ["outer"],
    )
    output = await investigation(report, index)
    (table,) = output.table["table"]
    assert "caption" not in table
    assert output.table["caption"].startswith("outer (")
    rows = table["tbody"]["tr"]
    heading = rows[0]["td"][0]
    assert heading["@colspan"] == 4
    assert heading["content"]["@styleCode"] == "allIndent"
    assert heading["content"]["content"] == {"@styleCode": "Bold", "#text": "Test group: inner"}
    deeper = rows[2]["td"][0]["content"]
    assert deeper["@styleCode"] == deeper["content"]["@styleCode"] == "allIndent"
    assert deeper["content"]["content"]["#text"] == "Test group: deeper"
    assert len(output.organizer["component"]) == 2  # Panel value and false both survive.


@pytest.mark.asyncio
async def test_shared_comment_remains_visible_when_other_results_move_below_panels():
    report, index = report_and_index(
        [
            observation("standalone", valueString="Independent"),
            observation("panel", [("has-member", "result")]),
            observation("result", valueString="Result"),
            observation(
                "note",
                [("derived-from", "standalone"), ("derived-from", "panel")],
                comment_note=True,
                comment="Shared comment",
            ),
        ],
        ["standalone", "panel"],
    )
    output = await investigation(report, index)
    panel, other = output.table["table"]
    assert panel["caption"]["#text"] == "panel"
    assert other["caption"]["#text"] == "Other results"
    assert "shown elsewhere in this report" in xmltodict.unparse({"table": panel})
    assert "shown above" not in xmltodict.unparse({"table": panel})
    assert "Shared comment" in xmltodict.unparse({"table": other})


@pytest.mark.asyncio
async def test_cycle_and_missing_references_keep_notice_and_unplaced_content():
    report, index = report_and_index(
        [observation("cycle", [("has-member", "cycle")], valueString="Available result")],
        ["cycle", "missing"],
    )
    output = await investigation(report, index)
    assert output.table["table"][0]["caption"]["#text"] == "Unplaced source items"
    assert "could not be resolved" in output.table["paragraph"][0]
    assert "Available result" in xmltodict.unparse({"report": output.table})
    assert len(output.organizer["component"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("conclusion", [None, "Report interpretation"])
async def test_report_filing_is_a_list_before_tables_and_after_conclusion(conclusion):
    report, index = report_and_index(
        [
            observation("result", valueString="Result"),
            observation("first", comment_note=True, valueString="Filing value", comment="A < B & C\nNext line"),
            observation("second", comment_note=True, comment="Second comment"),
            observation("attached", [("derived-from", "result")], comment_note=True, comment="Attached comment"),
        ],
        ["result", "first", "second", "first", "attached"],
    )
    report.conclusion = conclusion
    output = await investigation(report, index)
    filing = output.table["list"]
    assert filing["@listType"] == "unordered"
    assert len(filing["item"]) == 2
    assert filing["item"][0]["paragraph"][1:] == ["Filing value", "A < B & C\nNext line"]
    assert filing["item"][1]["paragraph"][1:] == ["Second comment"]
    assert len(output.organizer["component"]) == 1
    xml = xmltodict.unparse({"item": output.table})
    assert xml.index('<list listType="unordered">') < xml.index("<table>")
    if conclusion:
        assert xml.index(conclusion) < xml.index('<list listType="unordered">')
    assert "A &lt; B &amp; C\nNext line" in xml
    assert "Attached comment" not in xmltodict.unparse({"list": filing})
    assert "Attached comment" in xmltodict.unparse({"tables": {"table": output.table["table"]}})
    assert "Second comment" not in xmltodict.unparse({"tables": {"table": output.table["table"]}})


@pytest.mark.asyncio
async def test_absent_report_filing_does_not_add_empty_list():
    report, index = report_and_index([observation("result", valueString="Found")], ["result"])
    output = await investigation(report, index)
    assert "list" not in output.table


@pytest.mark.asyncio
async def test_report_with_only_filing_comments_has_no_empty_result_table():
    report, index = report_and_index([observation("filing", comment_note=True, comment="Follow up")], ["filing"])
    output = await investigation(report, index)
    assert output.table["table"] == []
    assert output.table["list"]["item"][0]["paragraph"][1] == "Follow up"
    assert output.organizer["component"] == []
    assert "<table" not in xmltodict.unparse({"item": output.table})
