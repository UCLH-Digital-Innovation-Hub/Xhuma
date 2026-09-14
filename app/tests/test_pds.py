import json
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.pds.pds import lookup_patient, pds_cache_key, sds_cache_key, sds_trace


@patch("app.pds.pds.redis_client")
@patch("app.pds.pds.httpx.post")
@patch("app.pds.pds.httpx.AsyncClient")
@pytest.mark.asyncio
async def test_get_data_success(mock_async_client, mock_post, mock_redis):
    # --- mock redis: no token exists ---
    mock_redis.exists.return_value = False
    mock_redis.get.return_value = None
    mock_redis.setex.return_value = True  # avoid failure

    # --- mock token response ---
    mock_post.return_value.text = json.dumps({"access_token": "fake-token", "expires_in": 300})

    # --- mock patient response ---
    mock_client_instance = AsyncMock()
    mock_response = MagicMock()
    mock_response.text = json.dumps({"resourceType": "Patient", "id": "9690937278"})

    mock_client_instance.get.return_value = mock_response
    mock_async_client.return_value.__aenter__.return_value = mock_client_instance

    mock_request = MagicMock()
    mock_request.app.state.jwk_json = {"keys": [{"kid": "test-1"}]}

    patient = await lookup_patient(9690937278, request=mock_request)

    assert patient["resourceType"] == "Patient"
    assert patient["id"] == "9690937278"
    mock_redis.setex.assert_any_call(
        pds_cache_key(9690937278),
        24 * 60 * 60,
        json.dumps({"resourceType": "Patient", "id": "9690937278"}),
    )


@patch("app.pds.pds.redis_client")
@patch("app.pds.pds.httpx.AsyncClient")
@pytest.mark.asyncio
async def test_lookup_patient_returns_cached_result(mock_async_client, mock_redis):
    mock_redis.get.return_value = json.dumps({"resourceType": "Patient", "id": "9690937278"}).encode("utf-8")

    patient = await lookup_patient(9690937278)

    assert patient == {"resourceType": "Patient", "id": "9690937278"}
    mock_async_client.assert_not_called()
    mock_redis.setex.assert_not_called()


@patch("app.pds.pds.redis_client")
@patch("app.pds.pds.httpx.get")
@pytest.mark.asyncio
async def test_sds_trace_caches_result(mock_get, mock_redis):
    mock_redis.get.return_value = None
    mock_get.return_value.status_code = 200
    mock_get.return_value.text = json.dumps({"resourceType": "Bundle"})

    trace = await sds_trace("A82038")

    assert trace == {"resourceType": "Bundle"}
    mock_redis.setex.assert_called_once_with(
        "pds:sds:device:A82038",
        12 * 60 * 60,
        json.dumps({"resourceType": "Bundle"}),
    )


@patch("app.pds.pds.redis_client")
@patch("app.pds.pds.httpx.get")
@pytest.mark.asyncio
async def test_sds_trace_returns_cached_result(mock_get, mock_redis):
    mock_redis.get.return_value = json.dumps({"resourceType": "Bundle"}).encode()

    trace = await sds_trace("A82038")

    assert trace == {"resourceType": "Bundle"}
    mock_get.assert_not_called()
    mock_redis.setex.assert_not_called()


def test_cache_keys_are_deterministic():
    patient_key = pds_cache_key(9690937278, secret="test-cache-secret")
    assert patient_key == pds_cache_key(9690937278, secret="test-cache-secret")
    assert "9690937278" not in patient_key
    assert re.fullmatch(r"pds:patient:[0-9a-f]{64}", patient_key)
    assert patient_key != pds_cache_key(9690937279, secret="test-cache-secret")
    assert patient_key != pds_cache_key(9690937278, secret="different-secret")
    assert sds_cache_key("a82038") == sds_cache_key("A82038")
    assert sds_cache_key("A82038", endpoint=True, partykey="party-1") != sds_cache_key(
        "A82038", endpoint=True, partykey="party-2"
    )
