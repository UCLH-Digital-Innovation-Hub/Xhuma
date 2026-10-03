"""Validate result value datatypes, source preservation and comparator semantics."""

import pytest
import xmltodict
from fhirclient.models.observation import Observation

from app.ccda.entries.result_values import map_result_value
from app.ccda.entries.results import create_result_component
from app.ccda.models.base import ResultsOrganizer
from app.ccda.models.datatypes import BL, CD, INT, IVL_PQ, IVXB_PQ, PQ, PQR, RTO_PQ_PQ, ST
from app.tests.test_investigation_grouping import observation


def serialized(value, *, nested=False):
    """Compare XML semantics while allowing explicit xsi:type on typed children."""
    if hasattr(value, "model_dump"):
        value = value.model_dump(by_alias=True, exclude_none=True)
    if isinstance(value, dict):
        return {
            key: serialized(item, nested=True) for key, item in value.items() if not (nested and key == "@xsi:type")
        }
    if isinstance(value, list):
        return [serialized(item, nested=True) for item in value]
    return value


@pytest.mark.parametrize(
    "fields,expected,text",
    [
        (
            {"valueString": "A < B & C\nsecond line"},
            {"@xsi:type": "ST", "#text": "A < B & C\nsecond line"},
            "A < B & C\nsecond line",
        ),
        ({"valueBoolean": False}, {"@xsi:type": "BL", "@value": "false"}, "False"),
        ({"valueBoolean": True}, {"@xsi:type": "BL", "@value": "true"}, "True"),
        (
            {
                "valueQuantity": {
                    "value": 0,
                    "unit": "milligrams per litre",
                    "system": "http://unitsofmeasure.org",
                    "code": "mg/L",
                }
            },
            {"@xsi:type": "PQ", "@value": 0, "@unit": "mg/L"},
            "0 milligrams per litre",
        ),
    ],
)
@pytest.mark.asyncio
async def test_values_survive_full_result_serialization(fields, expected, text):
    result = await create_result_component(observation("test", **fields))
    assert result.row.cells[1] == text
    organizer = ResultsOrganizer(component=[{"observation": result.entry}])
    data = organizer.model_dump(by_alias=True, exclude_none=True)
    assert serialized(data["component"][0]["observation"]["value"]) == expected
    restored = ResultsOrganizer.model_validate(data)
    assert restored.model_dump(by_alias=True, exclude_none=True) == data
    parsed = xmltodict.parse(xmltodict.unparse({"value": expected}), strip_whitespace=False)["value"]
    assert parsed["@xsi:type"] == expected["@xsi:type"]
    if "#text" in expected:
        assert parsed["#text"] == expected["#text"]


def test_integer_zero_with_model_support():
    # STU3 Observation does not expose valueInteger. Exercise the helper with
    # a typed derived model to cover it without claiming raw STU3 accepts it.
    class IntegerObservation(Observation):
        valueInteger = 0

        def elementProperties(self):
            """Expose an integer choice when a supported input model provides it."""
            return super().elementProperties() + [("valueInteger", "valueInteger", int, False, "value", False)]

    result = map_result_value(IntegerObservation())
    assert isinstance(result.value, INT)
    assert serialized(result.value) == {"@xsi:type": "INT", "@value": 0}
    assert result.text == "0"


@pytest.mark.parametrize(
    "comparator,bound,inclusive",
    [("<", "high", "false"), ("<=", "high", "true"), (">", "low", "false"), (">=", "low", "true")],
)
def test_comparators_keep_zero_bound_and_explicit_inclusivity(comparator, bound, inclusive):
    result = map_result_value(observation("test", valueQuantity={"value": 5, "unit": "mg/L", "comparator": comparator}))
    assert serialized(result.value)["@xsi:type"] == "IVL_PQ"
    assert serialized(result.value)[bound] == {"@value": 5, "@unit": "mg/L", "@inclusive": inclusive}
    if comparator.startswith("<"):
        assert serialized(result.value)["low"] == {"@value": 0, "@unit": "mg/L", "@inclusive": "true"}
    else:
        assert serialized(result.value)["high"] == {"@nullFlavor": "PINF"}
    parsed = xmltodict.parse(xmltodict.unparse({"value": serialized(result.value)}))["value"]
    assert parsed[bound]["@inclusive"] == inclusive


def test_coded_result_preserves_all_codings_original_text_and_source_order():
    source = observation(
        "test",
        valueCodeableConcept={
            "text": "Not detected locally",
            "coding": [
                {"system": "urn:oid:1.2.3.4", "code": "NEG", "display": "Negative"},
                {"system": "http://snomed.info/sct", "code": "260415000", "display": "Not detected"},
                {"system": "https://example.test/local", "code": "N", "display": "Local negative"},
            ],
        },
    )
    before = source.as_json()
    result = map_result_value(source)
    assert serialized(result.value)["@code"] == "260415000"
    assert serialized(result.value)["@codeSystem"] == "2.16.840.1.113883.6.96"
    assert serialized(result.value)["originalText"] == "Not detected locally"
    assert serialized(result.value)["translation"][0]["@code"] == "NEG"
    assert (
        "https://example.test/local | N | Local negative" in serialized(result.value)["translation"][1]["originalText"]
    )
    assert source.as_json() == before


def test_text_only_concept_does_not_invent_a_code():
    result = map_result_value(observation("test", valueCodeableConcept={"text": "No growth"}))
    assert serialized(result.value) == {"@xsi:type": "CD", "@nullFlavor": "OTH", "originalText": "No growth"}
    assert result.text == "No growth"


@pytest.mark.parametrize("unit", ["customUnits", None])
def test_unverified_or_missing_units_preserve_magnitude_without_guessing(unit):
    quantity = {"value": 3}
    if unit:
        quantity["unit"] = unit
    result = map_result_value(observation("test", valueQuantity=quantity))
    assert serialized(result.value)["@nullFlavor"] == "OTH"
    assert "@unit" not in serialized(result.value)
    assert serialized(result.value)["translation"][0]["@value"] == 3
    assert serialized(result.value)["translation"][0]["originalText"] == (unit or "Unit not supplied")


@pytest.mark.parametrize("low,high", [(0, 5), (None, 5), (0, None)])
def test_ranges_only_emit_supplied_bounds(low, high):
    data = {k: {"value": v, "unit": "mg/L"} for k, v in [("low", low), ("high", high)] if v is not None}
    result = map_result_value(observation("test", valueRange=data))
    assert serialized(result.value)["@xsi:type"] == "IVL_PQ"
    for key, amount in [("low", low), ("high", high)]:
        if amount is None:
            assert key not in serialized(result.value)
        else:
            assert serialized(result.value)[key] == {"@value": amount, "@unit": "mg/L", "@inclusive": "true"}


def test_ratio_retains_each_unit_and_zero_numerator():
    result = map_result_value(
        observation(
            "test", valueRatio={"numerator": {"value": 0, "unit": "mg"}, "denominator": {"value": 2, "unit": "L"}}
        )
    )
    assert serialized(result.value) == {
        "@xsi:type": "RTO_PQ_PQ",
        "numerator": {"@value": 0, "@unit": "mg"},
        "denominator": {"@value": 2, "@unit": "L"},
    }
    assert result.text == "0 mg / 2 L"


@pytest.mark.parametrize(
    "fields",
    [
        {"valueAttachment": {"contentType": "text/plain", "data": "VGVzdA==", "title": "Laboratory attachment"}},
        {"valueDateTime": "2024-01-01T12:00:00Z"},
        {"valueRatio": {"numerator": {"value": 1}, "denominator": {"value": 0}}},
    ],
)
def test_unsupported_or_invalid_values_are_preserved_and_logged(fields, caplog):
    result = map_result_value(observation("test", **fields))
    assert serialized(result.value)["@xsi:type"] == "ST"
    assert serialized(result.value)["#text"] == result.text
    assert next(iter(fields)) in result.text
    assert "unmapped result value" in caplog.text


@pytest.mark.asyncio
async def test_absent_quantity_keeps_reason_and_comment_without_zero():
    source = observation(
        "test",
        valueQuantity={"unit": "mg/L"},
        comment="Insufficient sample",
        referenceRange=[{"low": {"value": 1}, "high": {"value": 5}}],
    )
    result = await create_result_component(source)
    assert serialized(result.entry.value) == {"@xsi:type": "PQ", "@nullFlavor": "UNK"}
    assert "Quantity not supplied" in result.row.cells[1]
    assert result.entry.text == "Insufficient sample"


def test_absent_reason_and_comment_only_are_distinct():
    source = observation(
        "test",
        dataAbsentReason={
            "coding": [{"system": "http://hl7.org/fhir/data-absent-reason", "code": "masked", "display": "Withheld"}]
        },
    )
    result = map_result_value(source)
    assert serialized(result.value) == {"@xsi:type": "ST", "@nullFlavor": "MSK"}
    assert result.text == "No value supplied: Withheld"
    assert map_result_value(observation("test", comment="Value 8.8 in original prose")).value is None


@pytest.mark.parametrize("value,comparator", [(-5, "<"), (-5, "<="), (0, "<")])
def test_zero_bound_convention_does_not_create_an_impossible_interval(value, comparator):
    result = map_result_value(
        observation("test", valueQuantity={"value": value, "unit": "mg/L", "comparator": comparator})
    )
    assert serialized(result.value)["@xsi:type"] == "ST"
    assert str(value) in result.text
    assert comparator in result.text


@pytest.mark.parametrize(
    "fields,model",
    [
        ({"valueString": "text"}, ST),
        ({"valueBoolean": False}, BL),
        ({"valueCodeableConcept": {"text": "No growth"}}, CD),
        ({"valueQuantity": {"value": 5, "unit": "mg/L"}}, PQ),
        ({"valueQuantity": {"value": 5, "unit": "mg/L", "comparator": "<"}}, IVL_PQ),
        ({"valueRange": {"low": {"value": 1, "unit": "mg/L"}}}, IVL_PQ),
        (
            {"valueRatio": {"numerator": {"value": 1, "unit": "mg"}, "denominator": {"value": 2, "unit": "L"}}},
            RTO_PQ_PQ,
        ),
        ({"valueDateTime": "2024-01-01T12:00:00Z"}, ST),
        ({"dataAbsentReason": {"text": "Not available"}}, ST),
    ],
)
@pytest.mark.asyncio
async def test_mapper_returns_cda_models_and_serializes_them_through_organizer(fields, model):
    source = observation("test", **fields)
    mapped = map_result_value(source)
    assert isinstance(mapped.value, model)
    converted = await create_result_component(source)
    assert isinstance(converted.entry.value, model)
    data = ResultsOrganizer(component=[{"observation": converted.entry}]).model_dump(by_alias=True, exclude_none=True)
    assert data["component"][0]["observation"]["value"] == mapped.value.model_dump(by_alias=True, exclude_none=True)
    xml = xmltodict.unparse({"organizer": data})
    xmltodict.parse(xml)
    assert "<inclusive>" not in xml
    assert '="False"' not in xml and '="True"' not in xml


def test_nested_cda_models_keep_null_flavors_and_translations():
    mapped = map_result_value(observation("test", valueQuantity={"value": 5, "unit": "customUnits", "comparator": "<"}))
    assert isinstance(mapped.value.low, IVXB_PQ)
    assert isinstance(mapped.value.high, IVXB_PQ)
    assert isinstance(mapped.value.high.translation[0], PQR)
    assert mapped.value.high.translation[0].value == 5
    xml = xmltodict.unparse({"value": mapped.value.model_dump(by_alias=True, exclude_none=True)})
    assert 'inclusive="false"' in xml
    assert 'value="5.0"' in xml
    assert "<value>" not in xml
    restored = IVL_PQ.model_validate(mapped.value.model_dump(by_alias=True, exclude_none=True))
    assert restored.high.inclusive is False
    coded = map_result_value(
        observation("test", valueCodeableConcept={"coding": [{"system": "https://unknown.test", "code": "X"}]})
    )
    assert isinstance(coded.value, CD)
    assert isinstance(coded.value.translation[0], CD)
    assert coded.value.code is None and coded.value.nullFlavor == "OTH"
