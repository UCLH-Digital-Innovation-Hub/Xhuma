"""Check original result names and report/sample text remain in the CCDA."""

import json
from pathlib import Path

import pytest
import xmltodict
from fhirclient.models.bundle import Bundle
from fhirclient.models.diagnosticreport import DiagnosticReport
from fhirclient.models.fhirreference import FHIRReference
from fhirclient.models.specimen import Specimen

from app.ccda.entries.investigation_grouping import group_investigation
from app.ccda.entries.results import create_result_component, investigation, specimen_notes_table
from app.tests.test_investigation_grouping import observation, report_and_index


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "system,original", [("http://snomed.info/sct", "Serum potassium"), ("https://example.test/codes", None)]
)
async def test_degraded_original_name_preserves_coding_and_comment(system, original):
    source = observation(
        "degraded",
        code={
            "coding": [{"system": system, "code": "196411000000103", "display": "Transfer-degraded record entry"}],
            "text": "Serum potassium",
        },
        comment="Source comment",
        valueQuantity={"value": 4.5, "unit": "mmol/L"},
    )
    output = await create_result_component(source)
    assert output.row.cells[0] == (original or "Transfer-degraded record entry")
    assert output.entry.code.code == "196411000000103"
    assert output.entry.code.displayName == "Transfer-degraded record entry"
    assert output.entry.code.originalText == original
    assert output.entry.text == "Source comment"
    xml = xmltodict.unparse({"observation": output.entry.model_dump(by_alias=True, exclude_none=True)})
    if original:
        assert "<originalText>Serum potassium</originalText>" in xml


@pytest.mark.asyncio
async def test_conclusion_and_distinct_specimen_notes_survive_in_order():
    report, index = report_and_index([observation("test", valueString="Negative")], ["test"])
    report.conclusion = "Clinical details:\nA < B & follow up"
    for name in ("serum", "urine", "unrelated"):
        specimen = Specimen(
            {
                "resourceType": "Specimen",
                "subject": {"reference": "Patient/test"},
                "id": name,
                "type": {"text": name},
                "note": [{"text": "Line one\nLine two"}, {"text": f"Note for {name}"}],
            }
        )
        index[f"Specimen/{name}"] = specimen
    index["urn:uuid:serum"] = index["Specimen/serum"]
    report.specimen = [FHIRReference({"reference": r}) for r in ("Specimen/serum", "urn:uuid:serum", "Specimen/urine")]
    output = await investigation(report, index)
    assert output.table["paragraph"][1] == report.conclusion
    assert len(output.table["table"]) == 2
    notes = output.table["table"][1]
    assert notes["caption"] == "Specimen notes"
    assert len(notes["tbody"]["tr"]) == 4
    assert notes["tbody"]["tr"][0]["td"][0].startswith("serum")
    assert notes["tbody"]["tr"][2]["td"][0].startswith("urine")
    xml = xmltodict.unparse({"item": output.table})
    assert "Note for unrelated" not in xml
    assert xml.index("Report Interpretation") < xml.index("<table>") < xml.index("Specimen notes")
    assert "A &lt; B &amp; follow up" in xml
    assert len(output.organizer["component"]) == 1  # No invented result for prose.


def test_no_empty_specimen_notes_table():
    report, index = report_and_index([], [])
    assert specimen_notes_table(report, index) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("nhs", ["9730333939", "9465700088", "9692136744"])
async def test_saved_bundles_preserve_original_labels_conclusions_and_specimen_notes(nhs):
    path = Path(__file__).parent / "fixtures/bundles/investigations" / f"{nhs}.json"
    bundle = Bundle(json.loads(path.read_text()))
    index = {f"{e.resource.resource_type}/{e.resource.id}": e.resource for e in bundle.entry}
    for report in (e.resource for e in bundle.entry if isinstance(e.resource, DiagnosticReport)):
        output = await investigation(report, index)
        narrative = json.dumps(output.table, ensure_ascii=False)
        if report.conclusion:
            assert output.table["paragraph"][1] == report.conclusion
        for source in group_investigation(report, index).observations.values():
            if source.comment:
                assert json.dumps(source.comment, ensure_ascii=False)[1:-1] in narrative
            if (
                source.code
                and source.code.text
                and any(
                    c.system == "http://snomed.info/sct" and c.code == "196411000000103"
                    for c in source.code.coding or []
                )
            ):
                assert json.dumps(source.code.text, ensure_ascii=False)[1:-1] in narrative
        for reference in report.specimen or []:
            specimen = index[reference.reference]
            for note in specimen.note or []:
                if note.text:
                    notes = next(t for t in output.table["table"] if t.get("caption") == "Specimen notes")["tbody"][
                        "tr"
                    ]
                    assert any(row["td"][1] == note.text for row in notes)
