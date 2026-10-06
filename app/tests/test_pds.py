import asyncio
import json
import re
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException

from app.pds.pds import lookup_patient, pds_cache_key, sds_cache_key, sds_trace
from app.tests.fixtures.saml_attributes import saml


@patch("app.pds.pds.attempt_audit", new_callable=AsyncMock)
@patch("app.pds.pds.redis_client", autospec=True)
@patch("app.pds.pds.httpx.AsyncClient")
@pytest.mark.asyncio
async def test_get_data_success(mock_async_client, mock_redis, mock_attempt_audit):
    # --- mock redis: no token exists ---
    mock_redis.exists.return_value = False
    mock_redis.get.return_value = None
    mock_redis.setex.return_value = True  # avoid failure

    # --- mock token response ---
    token_response = MagicMock()
    token_response.text = json.dumps({"access_token": "fake-token", "expires_in": 300})

    # --- mock patient response ---
    mock_client_instance = AsyncMock()
    mock_response = MagicMock()
    mock_response.text = json.dumps({"resourceType": "Patient", "id": "9690937278"})

    mock_client_instance.post.return_value = token_response
    mock_client_instance.get.return_value = mock_response
    mock_async_client.return_value.__aenter__.return_value = mock_client_instance

    mock_request = MagicMock()
    mock_request.app.state.jwk_json = {"keys": [{"kid": "test-1"}]}

    patient = await lookup_patient(9690937278, request=mock_request, saml=saml)

    mock_client_instance.post.assert_awaited_once()
    mock_client_instance.get.assert_awaited_once()
    mock_redis.setex.assert_any_await("access_token", 300, "fake-token")
    assert patient["resourceType"] == "Patient"
    assert patient["id"] == "9690937278"
    mock_redis.setex.assert_any_await(
        pds_cache_key(9690937278),
        24 * 60 * 60,
        json.dumps({"resourceType": "Patient", "id": "9690937278"}),
    )


@patch("app.pds.pds.attempt_audit", new_callable=AsyncMock)
@patch("app.pds.pds.redis_client", autospec=True)
@patch("app.pds.pds.httpx.AsyncClient")
@pytest.mark.asyncio
async def test_lookup_patient_returns_cached_result(mock_async_client, mock_redis, mock_attempt_audit):
    mock_redis.get.return_value = json.dumps({"resourceType": "Patient", "id": "9690937278"}).encode("utf-8")

    patient = await lookup_patient(9690937278, saml=saml)

    assert patient == {"resourceType": "Patient", "id": "9690937278"}
    mock_async_client.assert_not_called()
    mock_redis.setex.assert_not_called()


@patch("app.pds.pds.redis_client", autospec=True)
@patch("app.pds.pds.httpx.AsyncClient")
@pytest.mark.asyncio
async def test_sds_trace_caches_result(mock_async_client, mock_redis):
    mock_redis.get.return_value = None
    client = AsyncMock()
    client.get.return_value = httpx.Response(200, json={"resourceType": "Bundle"})
    mock_async_client.return_value.__aenter__.return_value = client

    trace = await sds_trace("A82038")

    assert trace == {"resourceType": "Bundle"}
    client.get.assert_awaited_once()
    mock_redis.setex.assert_awaited_once_with(
        "pds:sds:device:A82038",
        12 * 60 * 60,
        json.dumps({"resourceType": "Bundle"}),
    )


@patch("app.pds.pds.redis_client", autospec=True)
@patch("app.pds.pds.httpx.AsyncClient")
@pytest.mark.asyncio
async def test_sds_trace_returns_cached_result(mock_async_client, mock_redis):
    mock_redis.get.return_value = json.dumps({"resourceType": "Bundle"}).encode()

    trace = await sds_trace("A82038")

    assert trace == {"resourceType": "Bundle"}
    mock_async_client.assert_not_called()
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


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["pds_token", "sds_device", "sds_endpoint"])
async def test_upstream_wait_yields_and_cancellation_closes_client(operation):
    """A paused upstream request must let other tasks run and remain cancellable."""
    entered = asyncio.Event()
    release = asyncio.Event()

    async def paused_response(*args, **kwargs):
        entered.set()
        await release.wait()
        raise AssertionError("The pending request should have been cancelled")

    client = AsyncMock()
    client.post.side_effect = paused_response
    client.get.side_effect = paused_response
    with (
        patch("app.pds.pds.redis_client", autospec=True) as cache,
        patch("app.pds.pds.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.pds.pds.pds_jwt", return_value="signed-assertion"),
        patch("app.pds.pds.httpx.AsyncClient") as factory,
        patch("app.pds.pds.httpx.get", side_effect=AssertionError("Synchronous HTTP used")),
        patch("app.pds.pds.httpx.post", side_effect=AssertionError("Synchronous HTTP used")),
    ):
        cache.get.return_value = None
        cache.exists.return_value = False
        factory.return_value.__aenter__.return_value = client
        if operation == "pds_token":
            task = asyncio.create_task(lookup_patient(9690937278, saml=saml))
        else:
            task = asyncio.create_task(sds_trace("A82038", endpoint=operation == "sds_endpoint", mhsparty="party-1"))
        try:
            await asyncio.wait_for(entered.wait(), timeout=1)
            assert not task.done()
            call = client.post.await_args if operation == "pds_token" else client.get.await_args
            if operation == "pds_token":
                assert call.args[0].endswith("oauth2/token")
                assert call.kwargs["data"]["client_assertion"] == "signed-assertion"
            else:
                suffix = "Endpoint" if operation == "sds_endpoint" else "Device"
                assert call.args[0].endswith(f"spine-directory/FHIR/R4/{suffix}")
                assert call.kwargs["params"]["organization"].endswith("|A82038")
                if operation == "sds_endpoint":
                    assert call.kwargs["params"]["identifier"][1].endswith("|party-1")
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        factory.return_value.__aexit__.assert_awaited_once()
        cache.setex.assert_not_called()
        audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_token_timeout_is_audited_and_does_not_cache():
    with (
        patch("app.pds.pds.redis_client", autospec=True) as cache,
        patch("app.pds.pds.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.pds.pds.pds_jwt", return_value="signed-assertion"),
        patch("app.pds.pds.httpx.AsyncClient") as factory,
    ):
        cache.get.return_value = None
        cache.exists.return_value = False
        client = AsyncMock()
        client.post.side_effect = httpx.ReadTimeout("Token endpoint timed out")
        factory.return_value.__aenter__.return_value = client
        with pytest.raises(HTTPException) as exc:
            await lookup_patient(9690937278, saml=saml)
        assert exc.value.status_code == 502
        assert audit.await_args.kwargs["action"] == "pds_token_fetch"
        assert audit.await_args.kwargs["outcome"].value == "fail"
        client.get.assert_not_awaited()
        cache.setex.assert_not_called()
        factory.return_value.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
async def test_sds_failure_does_not_cache():
    with patch("app.pds.pds.redis_client", autospec=True) as cache, patch("app.pds.pds.httpx.AsyncClient") as factory:
        cache.get.return_value = None
        client = AsyncMock()
        client.get.return_value = httpx.Response(503, text="Unavailable")
        factory.return_value.__aenter__.return_value = client
        with pytest.raises(Exception, match="503: Unavailable"):
            await sds_trace("A82038")
        cache.setex.assert_not_called()
        factory.return_value.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [b"cached-token", "cached-token"])
async def test_cached_token_uses_one_read_without_exists_race(token):
    with (
        patch("app.pds.pds.redis_client", autospec=True) as cache,
        patch("app.pds.pds.attempt_audit", new_callable=AsyncMock),
        patch("app.pds.pds.httpx.AsyncClient") as factory,
    ):
        cache.get.side_effect = [None, token]
        client = AsyncMock()
        client.get.return_value = httpx.Response(
            200, json={"resourceType": "Patient"}, request=httpx.Request("GET", "https://example.test/patient")
        )
        factory.return_value.__aenter__.return_value = client
        assert await lookup_patient(9690937278, saml=saml) == {"resourceType": "Patient"}
        assert [call.args for call in cache.get.await_args_list] == [(pds_cache_key(9690937278),), ("access_token",)]
        cache.exists.assert_not_called()
        client.post.assert_not_awaited()
        assert client.get.await_args.kwargs["headers"]["Authorization"] == "Bearer cached-token"
