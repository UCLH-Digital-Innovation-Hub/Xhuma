from unittest.mock import AsyncMock, MagicMock, patch

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
@patch("app.ccda.dmd.snomed_client", autospec=True)
@patch("app.ccda.dmd.get_terminology_token", new_callable=AsyncMock)
@patch("app.ccda.dmd.httpx.AsyncClient")
async def test_dmd_lookup(mock_async_client, mock_get_token, mock_snomed):
    # Redis/cache behaviour
    mock_snomed.get.return_value = None
    mock_snomed.setex.return_value = True

    # Token
    mock_get_token.return_value = "fake-dmd-token"

    # Mock API responses, in order:
    # 1. main DMD concept
    # 2. unit concept
    # 3. route concept
    response_1 = MagicMock()
    response_1.status_code = 200
    response_1.json.return_value = example_return

    response_2 = MagicMock()
    response_2.status_code = 200
    response_2.json.return_value = example_unit_concept

    response_3 = MagicMock()
    response_3.status_code = 200
    response_3.json.return_value = example_route_concept

    mock_client_instance = AsyncMock()
    mock_client_instance.get.side_effect = [response_1, response_2, response_3]

    mock_async_client.return_value.__aenter__.return_value = mock_client_instance

    concept = await dmd_lookup(42010411000001105)

    assert concept.concept_id == 42010411000001105
    assert concept.valueString == "Codeine 30mg tablets"

    assert concept.vpi is not None
    assert concept.vpi.value == 30.0
    assert concept.vpi.unit == "gram"

    assert concept.route is not None
    assert concept.route.code == "26643006"
    assert concept.route.displayName == "Oral"

    assert mock_snomed.setex.await_count == 3


@pytest.mark.asyncio
async def test_prewarmed_concept_awaits_cache_without_http():
    import json

    from app.ccda.dmd import dmd_cache_key, get_dmd_concept

    with patch("app.ccda.dmd.snomed_client", autospec=True) as cache, patch("app.ccda.dmd.httpx.AsyncClient") as http:
        cache.get.return_value = json.dumps(example_return).encode()
        assert await get_dmd_concept(123, properties=["parent", "VPI"]) == example_return
        cache.get.assert_awaited_once_with(dmd_cache_key(123, ["parent", "VPI"]))
        cache.setex.assert_not_awaited()
        http.assert_not_called()


@pytest.mark.asyncio
async def test_terminology_token_cache_write_is_awaited():
    import httpx

    from app.ccda.dmd import get_terminology_token

    with (
        patch("app.ccda.dmd.client_id", "test-client"),
        patch("app.ccda.dmd.client_secret", "test-secret"),
        patch("app.ccda.dmd.snomed_client", autospec=True) as cache,
        patch("app.ccda.dmd.httpx.AsyncClient") as factory,
    ):
        client = AsyncMock()
        client.post.return_value = httpx.Response(
            200, json={"access_token": "cached-token"}, request=httpx.Request("POST", "https://example.test/token")
        )
        factory.return_value.__aenter__.return_value = client
        assert await get_terminology_token() == "cached-token"
        cache.setex.assert_awaited_once_with("dmd_token", 30 * 60, "cached-token")
