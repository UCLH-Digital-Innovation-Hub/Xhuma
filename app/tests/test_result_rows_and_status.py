"""Check fixed result columns and explicit FHIR-to-CDA status conversion."""

import pytest
import xmltodict

from app.ccda.entries.results import create_result_component, investigation, result_status
from app.ccda.models.datatypes import CS
from app.tests.test_investigation_grouping import observation, report_and_index


@pytest.mark.parametrize(
    "status,code,null_flavor",
    [
        ("registered", "active", None),
        ("partial", "active", None),
        ("preliminary", "active", None),
        ("final", "completed", None),
        ("amended", "completed", None),
        ("corrected", "completed", None),
        ("appended", "completed", None),
        ("cancelled", "aborted", None),
        ("entered-in-error", None, "OTH"),
        ("unknown", None, "UNK"),
        (None, None, "UNK"),
        ("unrecognised", None, "OTH"),
    ],
)
@pytest.mark.asyncio
async def test_observation_and_organizer_status_mapping(status, code, null_flavor):
    source = observation("test", comment="Keep this comment", valueString="Result text")
    source.status = status
    report, index = report_and_index([source], ["test"])
    report.status = status
    output = await investigation(report, index)
    component = output.organizer["component"][0]["observation"]
    assert component["statusCode"].get("@code") == code
    assert component["statusCode"].get("@nullFlavor") == null_flavor
    expected_report_code = "nullified" if status == "entered-in-error" else code
    assert output.organizer["statusCode"].get("@code") == expected_report_code
    assert output.organizer["statusCode"].get("@nullFlavor") == (None if status == "entered-in-error" else null_flavor)
    if null_flavor == "OTH":
        assert f"Source result status: {status}" in component["text"]
        assert "Keep this comment" in component["text"]
        assert f"Source result status: {status}" in str(output.table)
    serialized = result_status(status).model_dump(by_alias=True, exclude_none=True)
    assert CS.model_validate(serialized).model_dump(by_alias=True, exclude_none=True) == serialized
    xml = xmltodict.unparse({"statusCode": serialized})
    if null_flavor:
        assert " code=" not in xml
        assert f'nullFlavor="{null_flavor}"' in xml


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    [
        {},
        {"valueString": "Not detected"},
        {"valueQuantity": {"value": 0, "unit": "mg/L"}},
        {"valueQuantity": {"value": 5, "unit": "mg/L", "comparator": "<"}},
        {"valueQuantity": {"value": 5, "unit": "mg/L", "comparator": ">"}},
    ],
)
@pytest.mark.parametrize("with_range", [False, True])
@pytest.mark.parametrize("with_comment", [False, True])
async def test_result_cells_keep_fixed_positions(value, with_range, with_comment):
    fields = dict(value)
    if with_range:
        fields["referenceRange"] = [{"text": "Laboratory range"}]
    if with_comment:
        fields["comment"] = "Laboratory comment"
    output = await create_result_component(observation("test", **fields))
    cells = output.row.cells
    assert len(cells) == 4
    assert cells[0] == "test"
    assert (cells[1] is not None) == bool(value)
    assert cells[2] == ({"#text": "Laboratory range"} if with_range else None)
    assert (cells[3] is not None) == with_comment
    if with_comment:
        assert "Laboratory comment" in str(cells[3])
