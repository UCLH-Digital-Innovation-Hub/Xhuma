import json
from unittest.mock import AsyncMock, patch

import pytest

from app.ccda.dmd import dmd_lookup

example_return = {
    "parameter": [
        {"name": "code", "valueCode": "42010411000001105"},
        {"name": "display", "valueString": "Codeine 30mg tablets"},
        {"name": "name", "valueString": "Dictionary of medicines and devices (dm+d)"},
        {
            "name": "property",
            "part": [
                {"name": "code", "valueCode": "ROUTECD"},
                {
                    "name": "value",
                    "valueCoding": {"code": "26643006", "system": "https://dmd.nhs.uk"},
                },
            ],
        },
        {
            "name": "property",
            "part": [
                {"name": "code", "valueCode": "VPI"},
                {
                    "name": "subproperty",
                    "part": [
                        {"name": "code", "valueCode": "ISID"},
                        {
                            "name": "value",
                            "valueCoding": {
                                "code": "261000",
                                "system": "https://dmd.nhs.uk",
                            },
                        },
                        {
                            "name": "valueCoding",
                            "valueCoding": {
                                "code": "261000",
                                "system": "https://dmd.nhs.uk",
                            },
                        },
                    ],
                },
                {
                    "name": "subproperty",
                    "part": [
                        {"name": "code", "valueCode": "BASIS_STRNTCD"},
                        {
                            "name": "value",
                            "valueCoding": {
                                "code": "1",
                                "system": "https://dmd.nhs.uk/BASIS_OF_STRNTH",
                            },
                        },
                        {
                            "name": "valueCoding",
                            "valueCoding": {
                                "code": "1",
                                "system": "https://dmd.nhs.uk/BASIS_OF_STRNTH",
                            },
                        },
                    ],
                },
                {
                    "name": "subproperty",
                    "part": [
                        {"name": "code", "valueCode": "STRNT_NMRTR_VAL"},
                        {"name": "value", "valueDecimal": 30.0},
                        {"name": "valueDecimal", "valueDecimal": 30.0},
                    ],
                },
                {
                    "name": "subproperty",
                    "part": [
                        {"name": "code", "valueCode": "STRNT_NMRTR_UOMCD"},
                        {
                            "name": "value",
                            "valueCoding": {
                                "code": "258684004",
                                "system": "https://dmd.nhs.uk",
                            },
                        },
                        {
                            "name": "valueCoding",
                            "valueCoding": {
                                "code": "258684004",
                                "system": "https://dmd.nhs.uk",
                            },
                        },
                    ],
                },
            ],
        },
    ],
    "resourceType": "Parameters",
}

example_unit_concept = {
    "parameter": [
        {"name": "code", "valueCode": "258682000"},
        {"name": "display", "valueString": "gram"},
        {"name": "name", "valueString": "Dictionary of medicines and devices (dm+d)"},
        {"name": "system", "valueUri": "https://dmd.nhs.uk"},
        {"name": "version", "valueString": "202603.2.0"},
        {
            "name": "property",
            "part": [
                {"name": "code", "valueCode": "inactive"},
                {"name": "value", "valueBoolean": False},
            ],
        },
        {
            "name": "property",
            "part": [
                {"name": "code", "valueCode": "parent"},
                {"name": "value", "valueCode": "UOM"},
            ],
        },
        {
            "name": "designation",
            "part": [
                {
                    "name": "use",
                    "valueCoding": {
                        "code": "display",
                        "system": "http://terminology.hl7.org/CodeSystem/designation-usage",
                    },
                },
                {"name": "value", "valueString": "gram"},
            ],
        },
    ],
    "resourceType": "Parameters",
}

example_route_concept = {
    "parameter": [
        {"name": "code", "valueCode": "26643006"},
        {"name": "display", "valueString": "Oral"},
        {"name": "name", "valueString": "Dictionary of medicines and devices (dm+d)"},
        {"name": "system", "valueUri": "https://dmd.nhs.uk"},
        {"name": "version", "valueString": "202603.2.0"},
        {
            "name": "property",
            "part": [
                {"name": "code", "valueCode": "inactive"},
                {"name": "value", "valueBoolean": False},
            ],
        },
        {
            "name": "property",
            "part": [
                {"name": "code", "valueCode": "parent"},
                {"name": "value", "valueCode": "ROUTE"},
            ],
        },
        {
            "name": "designation",
            "part": [
                {
                    "name": "use",
                    "valueCoding": {
                        "code": "display",
                        "system": "http://terminology.hl7.org/CodeSystem/designation-usage",
                    },
                },
                {"name": "value", "valueString": "Oral"},
            ],
        },
    ],
    "resourceType": "Parameters",
}


@pytest.mark.asyncio
@patch("app.ccda.dmd.httpx.get")
@patch("app.ccda.dmd.snomed_client.setex")
async def test_dmd_lookup(mock_get, mock_dmd):
    # mock each response in turn
    mock_get = AsyncMock(
        side_effect=[
            json.dumps(example_return),
            json.dumps(example_unit_concept),
            json.dumps(example_route_concept),
        ]
    )

    mock_dmd.patch.object(dmd_lookup, "httpx.get", mock_get)

    concept = await dmd_lookup("42010411000001105")
    assert concept.concept_id == 42010411000001105
    assert concept.valueString == "Codeine 30mg tablets"

    assert concept.vpi is not None
    assert concept.vpi.value == 30.0
    assert concept.vpi.unit == "mg"

    assert concept.route is not None
    assert concept.route.code == "26643006"
    assert concept.route.displayName == "Oral"
