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
        sds_cache_key("A82038"),
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
async def test_lookup_self_device_missing_ods_code_raises(monkeypatch):
    from app.pds.pds import lookup_self_device

    monkeypatch.delenv("XHUMA_ODS_CODE", raising=False)
    monkeypatch.setenv("ORG_ASID", "123")
    with pytest.raises(ValueError, match="XHUMA_ODS_CODE and ORG_ASID are required for the SDS self lookup"):
        await lookup_self_device()


@pytest.mark.asyncio
async def test_lookup_self_device_missing_org_asid_raises(monkeypatch):
    from app.pds.pds import lookup_self_device

    monkeypatch.setenv("XHUMA_ODS_CODE", "Z6G1Z")
    monkeypatch.delenv("ORG_ASID", raising=False)
    with pytest.raises(ValueError, match="XHUMA_ODS_CODE and ORG_ASID are required for the SDS self lookup"):
        await lookup_self_device()


@pytest.mark.asyncio
async def test_lookup_self_device_success(monkeypatch):
    from app.pds.pds import lookup_self_device

    monkeypatch.setenv("XHUMA_ODS_CODE", "Z6G1Z")
    monkeypatch.setenv("ORG_ASID", "200000002574")
    monkeypatch.delenv("XHUMA_MHS_PARTY_KEY", raising=False)

    fake_device_entry = {
        "resource": {
            "resourceType": "Device",
            "identifier": [
                {"system": "https://fhir.nhs.uk/Id/nhsSpineASID", "value": "200000002574"},
                {"system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey", "value": "TEST-PARTY-KEY"},
            ],
        }
    }
    fake_bundle = {
        "resourceType": "Bundle",
        "entry": [fake_device_entry],
    }

    with patch("app.pds.pds.sds_trace", new_callable=AsyncMock) as mock_sds:
        mock_sds.return_value = fake_bundle
        device = await lookup_self_device()
        assert device == fake_device_entry
        mock_sds.assert_awaited_once_with("Z6G1Z", mhsparty=None)


def test_terraform_app_service_xhuma_ods_code_configured():
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    variables_tf = (repo_root / "infra" / "variables.tf").read_text()
    main_tf = (repo_root / "infra" / "main.tf").read_text()
    play_tfvars = (repo_root / "infra" / "env" / "play.tfvars").read_text()
    int_tfvars = (repo_root / "infra" / "env" / "int.tfvars").read_text()

    assert 'variable "xhuma_ods_code"' in variables_tf
    assert '"XHUMA_ODS_CODE"    = var.xhuma_ods_code' in main_tf or '"XHUMA_ODS_CODE" = var.xhuma_ods_code' in main_tf
    assert "xhuma_ods_code" in play_tfvars and '"Z6G1Z"' in play_tfvars
    assert "xhuma_ods_code" in int_tfvars and '"Z6G1Z"' in int_tfvars
    # Ensure AUDIT_FAIL_OPEN and XHUMA_MHS_PARTY_KEY are unset in Terraform
    assert "AUDIT_FAIL_OPEN" not in main_tf
    assert "XHUMA_MHS_PARTY_KEY" not in main_tf
