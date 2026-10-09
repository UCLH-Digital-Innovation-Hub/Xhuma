import asyncio
import json
import os
import re
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

os.environ["NHS_API_KEY"] = "test_client_id"
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
    token_response.status_code = 200
    token_response.json.return_value = {"access_token": "fake-token", "expires_in": 300}

    # --- mock patient response ---
    mock_client_instance = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"resourceType": "Patient", "id": "9690937278"}

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


def test_nhs_api_environment_selection():
    import importlib
    import os

    import app.pds.pds

    # Test dev
    os.environ["ENV"] = "dev"
    importlib.reload(app.pds.pds)
    assert app.pds.pds.BASE_PATH == "https://dev.api.service.nhs.uk/"

    # Test int
    os.environ["ENV"] = "int"
    importlib.reload(app.pds.pds)
    assert app.pds.pds.BASE_PATH == "https://int.api.service.nhs.uk/"

    # Test prod
    os.environ["ENV"] = "prod"
    importlib.reload(app.pds.pds)
    assert app.pds.pds.BASE_PATH == "https://api.service.nhs.uk/"

    # Test unknown raises
    os.environ["ENV"] = "unknown"
    with pytest.raises(ValueError, match="Unknown or unsupported environment: unknown"):
        importlib.reload(app.pds.pds)

    # Restore to avoid side effects
    os.environ["ENV"] = "dev"
    importlib.reload(app.pds.pds)


@pytest.mark.asyncio
async def test_nhs_api_key_used_for_pds_and_sds(monkeypatch):
    monkeypatch.setenv("NHS_API_KEY", "nhs_special_key")
    monkeypatch.setenv("API_KEY", "xhuma_internal_key")

    with (
        patch("app.pds.pds.redis_client", autospec=True) as cache,
        patch("app.pds.pds.httpx.AsyncClient") as factory,
        patch("app.pds.pds.pds_jwt", return_value="jwt") as mock_pds_jwt,
        patch("app.pds.pds.attempt_audit"),
    ):
        cache.exists.return_value = False
        cache.get.return_value = None

        client = AsyncMock()
        post_resp = MagicMock()
        post_resp.status_code = 200
        post_resp.json.return_value = {"access_token": "token", "expires_in": 3600}
        client.post.return_value = post_resp

        get_resp = MagicMock()
        get_resp.status_code = 200
        get_resp.json.return_value = {"resourceType": "Bundle"}
        get_resp.text = '{"resourceType": "Bundle"}'
        client.get.return_value = get_resp
        factory.return_value.__aenter__.return_value = client

        await lookup_patient(9692140466, saml=saml)

        # Verify PDS JWT generation uses NHS_API_KEY
        mock_pds_jwt.assert_called_once_with(
            "nhs_special_key", "nhs_special_key", "https://dev.api.service.nhs.uk/oauth2/token", "test-1"
        )

        # Verify SDS trace header uses NHS_API_KEY
        await sds_trace("ods", "identifier")
        call_kwargs = client.get.call_args.kwargs
        assert call_kwargs["headers"]["apikey"] == "nhs_special_key"


@pytest.mark.asyncio
async def test_pds_error_classifications(monkeypatch):
    monkeypatch.setenv("NHS_API_KEY", "dummy")

    import fastapi

    from app.pds.pds import lookup_patient

    with (
        patch("app.pds.pds.redis_client", autospec=True) as cache,
        patch("app.pds.pds.httpx.AsyncClient") as factory,
        patch("app.pds.pds.attempt_audit", new_callable=AsyncMock),
    ):
        cache.exists.return_value = False

        async def _redis_get(key):
            if key == "access_token":
                return b"cached_token"
            return None

        cache.get.side_effect = _redis_get

        client = AsyncMock()
        factory.return_value.__aenter__.return_value = client

        # 404 test
        resp_404 = MagicMock()
        resp_404.status_code = 404
        resp_404.json.return_value = {"issue": [{"diagnostics": "Not found"}]}
        client.get.return_value = resp_404

        with pytest.raises(fastapi.HTTPException) as excinfo:
            await lookup_patient(12345, saml=MagicMock())
        assert excinfo.value.status_code == 404
        assert excinfo.value.detail == "Patient not found on PDS"

        # 401 test (Entitlement)
        resp_401 = MagicMock()
        resp_401.status_code = 401
        resp_401.json.return_value = {"issue": [{"diagnostics": "Unauthorised"}]}
        client.get.return_value = resp_401

        with pytest.raises(fastapi.HTTPException) as excinfo:
            await lookup_patient(12345, saml=MagicMock())
        assert excinfo.value.status_code == 502
        assert excinfo.value.detail == "PDS Entitlement or Token Failure"

        # Upstream Exception test (network failure)
        client.get.side_effect = Exception("Connection Refused")
        with pytest.raises(fastapi.HTTPException) as excinfo:
            await lookup_patient(12345, saml=MagicMock())
        assert excinfo.value.status_code == 502
        assert excinfo.value.detail == "PDS lookup upstream failure"


@pytest.mark.asyncio
async def test_pds_diagnostic_sanitization(caplog, monkeypatch):
    monkeypatch.setenv("NHS_API_KEY", "dummy")
    import httpx

    from app.pds.pds import lookup_patient

    with (
        patch("app.pds.pds.redis_client", autospec=True) as cache,
        patch("app.pds.pds.httpx.AsyncClient") as factory,
        patch("app.pds.pds.attempt_audit", new_callable=AsyncMock),
    ):
        cache.exists.return_value = False

        async def _redis_get_2(key):
            if key == "access_token":
                return b"cached_token"
            return None

        cache.get.side_effect = _redis_get_2

        client = AsyncMock()
        factory.return_value.__aenter__.return_value = client

        # Response with sensitive looking text and newlines
        sensitive_text = "Detailed error: user token BEARER 12345-ABC \n and Patient NHSNO: 9999999999 failed\r\n"
        resp_error = MagicMock()
        resp_error.status_code = 500
        resp_error.json.return_value = {"issue": [{"diagnostics": sensitive_text}]}
        resp_error.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500 Error", request=MagicMock(), response=resp_error
        )
        client.get.return_value = resp_error

        with pytest.raises(httpx.HTTPStatusError):
            await lookup_patient(12345, saml=MagicMock())

        # Verify sanitization stripped newlines and redacted sensitive info
        log_records = [r for r in caplog.records if "PDS API Failure" in r.message]
        assert len(log_records) == 1
        msg = log_records[0].message
        assert "\n" not in msg
        assert "\r" not in msg
        assert "9999999999" not in msg
        assert "[REDACTED_NHSNO]" in msg
        assert "12345-ABC" not in msg
        assert "Bearer [REDACTED_TOKEN]" in msg
