"""Reference-range fidelity and section completeness warnings."""

import pytest
import xmltodict
from fhirclient.models.bundle import Bundle

from app.ccda.entries.results import create_result_component
from app.ccda.fhir2ccda import convert_bundle
from app.tests.test_ccda_names import create_bundle_dict
from app.tests.test_investigation_grouping import observation


@pytest.mark.asyncio
async def test_ranges_keep_wrappers_text_and_independent_units():
    source = observation(
        "range",
        valueQuantity={"value": 10, "unit": "mg/L"},
        referenceRange=[
            {"text": "Adult range", "low": {"value": 0, "unit": "g/L"}, "high": {"value": 2, "unit": "g/L"}},
            {"text": "Negative"},
            {"high": {"value": 5, "unit": "custom units"}},
        ],
    )
    result = await create_result_component(source)
    ranges = result.entry.referenceRange
    assert len(ranges) == 3
    assert ranges[0].observationRange.value.low.value == 0
    assert ranges[0].observationRange.value.high.unit == "g/L"
    assert ranges[1].observationRange.value.text == "Negative"
    assert ranges[2].observationRange.value.high.translation[0].originalText == "custom units"
    assert isinstance(result.row.cells[1], str)
    xml = xmltodict.unparse({"observation": result.entry.model_dump(by_alias=True, exclude_none=True)})
    assert xml.count("<referenceRange ") == xml.count("<observationRange ") == 3
    assert "Adult range" in result.row.cells[2]["#text"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unit,qualified,flag", [("mg/L", False, True), ("g/L", False, False), (None, False, False), ("mg/L", True, False)]
)
async def test_abnormal_comparison_requires_matching_units_and_applicability(unit, qualified, flag):
    boundary = {"value": 5, **({"unit": unit} if unit else {})}
    source_range = {"high": boundary, **({"appliesTo": [{"text": "Children"}]} if qualified else {})}
    source = observation("range", valueQuantity={"value": 10, "unit": "mg/L"}, referenceRange=[source_range])
    result = await create_result_component(source)
    assert isinstance(result.row.cells[1], dict) == flag
    if unit is None:
        assert result.entry.referenceRange[0].observationRange.value.high.unit is None
    if qualified:
        assert "Children" in result.row.cells[2]["#text"]


@pytest.mark.asyncio
@pytest.mark.parametrize("empty", [True, False])
async def test_completeness_warning_survives_with_and_without_reports(empty, monkeypatch):
    monkeypatch.setenv("GP_CONNECT_INCLUDE_INVESTIGATIONS", "true")
    data = create_bundle_dict([{"family": "Test", "given": ["Patient"]}])
    warning = "Records before 2020 are incomplete.\nA < B & C"
    resource = {
        "resourceType": "List",
        "id": "investigations",
        "status": "current",
        "mode": "snapshot",
        "title": "Investigations and results",
        "note": [{"text": warning}],
    }
    if not empty:
        resource["entry"] = [{"item": {"reference": "DiagnosticReport/report"}}]
        data["entry"].append(
            {
                "resource": {
                    "resourceType": "DiagnosticReport",
                    "id": "report",
                    "identifier": [{"system": "https://example.test/report", "value": "report"}],
                    "status": "final",
                    "code": {"text": "Report"},
                    "subject": {"reference": "Patient/1"},
                }
            }
        )
    data["entry"].append({"resource": resource})
    bundle = Bundle(data)
    index = {f"{e.resource.resource_type}/{e.resource.id}": e.resource for e in bundle.entry}
    ccda = await convert_bundle(bundle, index)
    section = next(
        c["section"]
        for c in ccda["ClinicalDocument"]["component"]["structuredBody"]["component"]
        if c["section"]["code"]["@code"] == "30954-2"
    )
    assert section["text"]["paragraph"][0] == warning
    xml = xmltodict.unparse({"section": section})
    assert "A &lt; B &amp; C" in xml
    assert list(section["text"])[0] == "paragraph"
