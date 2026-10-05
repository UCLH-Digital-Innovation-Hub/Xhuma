"""Date conversion, display and calendar comparison contracts."""

from datetime import date

import pytest
import xmltodict
from fhirclient.models.fhirdate import FHIRDate
from fhirclient.models.period import Period

from app.ccda.helpers import (
    cda_time_bound,
    cda_time_interval,
    cda_timestamp,
    effective_time_helper,
    fhir_date_is_after,
    fhir_to_cda_timestamp,
    readable_date,
)
from app.ccda.models.base import SubstanceAdministration
from app.ccda.models.datatypes import IVL_TS, PIVL_TS, PQ


@pytest.mark.parametrize(
    "source,expected",
    [
        ("2024", "2024"),
        ("2024-03", "202403"),
        ("2024-03-15", "20240315"),
        ("2024-03-15T12:34:56.123Z", "20240315123456.123+0000"),
        ("2024-03-15T12:34:56-05:30", "20240315123456-0530"),
        ("2024-03-15T12:34:56+01:00", "20240315123456+0100"),
    ],
)
def test_source_precision(source, expected):
    assert fhir_to_cda_timestamp(FHIRDate(source)) == expected


@pytest.mark.parametrize(
    "source,expected",
    [
        ("20240315", "15/03/2024"),
        ("20240315123456.123+0100", "15/03/2024"),
        ("20240315003000+1400", "15/03/2024"),
        ("20240229", "29/02/2024"),
        ("202403", "2024-03"),
        ("2024", "2024"),
    ],
)
def test_readable_date(source, expected):
    assert readable_date(source) == expected


@pytest.mark.parametrize(
    "source",
    ["", "invalid", "15-03-2024", "20230229", "202413", "20240315250000", "20240315123456+1460", "20240315123456junk"],
)
def test_invalid_display_date(source):
    with pytest.raises(ValueError):
        readable_date(source)


def test_timestamp_and_boundary_xml_types():
    for helper, kind in [(cda_timestamp, "TS"), (cda_time_bound, "IVXB_TS")]:
        data = helper(FHIRDate("2024")).model_dump(by_alias=True, exclude_none=True)
        xml = xmltodict.unparse({"time": data})
        assert "<resource_type>" not in xml
        assert data["@xsi:type"] == kind
        assert data["@value"] == "2024"
        assert helper(None).model_dump(by_alias=True, exclude_none=True)["@nullFlavor"] == "UNK"
    assert fhir_to_cda_timestamp(None) is None
    interval = cda_time_interval(None, None)
    assert interval.low.nullFlavor == interval.high.nullFlavor == "UNK"


@pytest.mark.parametrize(
    "source,expected",
    [
        (None, False),
        ("2024", False),
        ("2025", True),
        ("2023", False),
        ("2024-03", False),
        ("2024-04", True),
        ("2024-02", False),
        ("2024-03-15", False),
        ("2024-03-16", True),
        ("2024-03-14", False),
        ("2024-03-15T23:59:59-12:00", False),
        ("2024-03-16T00:00:00+14:00", True),
    ],
)
def test_calendar_comparison(source, expected):
    assert fhir_date_is_after(FHIRDate(source) if source else None, date(2024, 3, 15)) is expected


@pytest.mark.parametrize(
    "source", [None, {}, {"start": "2024"}, {"end": "2024-03"}, {"start": "2024-03-15T12:34:56Z", "end": "2024-03-16"}]
)
def test_medication_intervals_and_missing_endpoints(source):
    times = effective_time_helper(Period(source) if source is not None else None)
    if not source:
        assert times == []
        return
    assert len(times) == 1 and isinstance(times[0], IVL_TS)
    for name, field in [("start", times[0].low), ("end", times[0].high)]:
        if name in source:
            assert field.value == fhir_to_cda_timestamp(FHIRDate(source[name]))
        else:
            assert field is None


def test_medication_serializes_duration_and_frequency_separately():
    duration = effective_time_helper(Period({"start": "2024-03-15T12:34:56Z"}))
    med = SubstanceAdministration(effectiveTime=[*duration, PIVL_TS(period=PQ(value=12, unit="h"))])
    data = med.model_dump(by_alias=True, exclude_none=True)
    times = data["effectiveTime"]
    assert len(times) == 2
    assert times[0]["@xsi:type"] == "IVL_TS"
    assert times[0]["low"]["@value"] == "20240315123456+0000"
    assert "high" not in times[0]
    assert times[1]["period"]["@value"] == 12
    xml = xmltodict.unparse({"substanceAdministration": data})
    assert xml.count("<effectiveTime ") == 2
