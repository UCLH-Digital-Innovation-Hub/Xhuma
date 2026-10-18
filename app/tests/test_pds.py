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
    token_response.status_code = 200
    token_response.json.return_value = {"access_token": "fake-token", "expires_in": 300}
    token_response.text = json.dumps({"access_token": "fake-token", "expires_in": 300})

    # --- mock patient response ---
    mock_client_instance = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"resourceType": "Patient", "id": "9690937278"}
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
async def test_sds_cache_write_timeout_can_leave_valid_cached_endpoint(monkeypatch):
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.trace import StatusCode

    from app import telemetry

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(telemetry, "tracer", provider.get_tracer("test"))
    trace = {"resourceType": "Bundle", "entry": [{"resource": {"address": "https://gp.example/fhir"}}]}
    saved = {}
    error = TimeoutError("SECRET_CACHE_CONNECTION_DETAILS")

    async def write_then_timeout(key, ttl, value):
        saved[key] = value
        raise error

    try:
        with (
            patch("app.pds.pds.redis_client", autospec=True) as cache,
            patch("app.pds.pds.httpx.AsyncClient") as factory,
        ):
            cache.get.return_value = None
            cache.setex.side_effect = write_then_timeout
            client = AsyncMock()
            client.get.return_value = httpx.Response(200, json=trace)
            factory.return_value.__aenter__.return_value = client

            with pytest.raises(TimeoutError) as caught:
                await sds_trace("H81058", endpoint=True, mhsparty="H81058-825269")

        assert caught.value is error
        key = sds_cache_key("H81058", endpoint=True, partykey="H81058-825269")
        assert json.loads(saved[key]) == trace
        spans = {span.name: span for span in exporter.get_finished_spans()}
        assert spans["xhuma.sds.cache.write"].status.status_code == StatusCode.ERROR
        assert spans["xhuma.sds.http"].attributes["stage.outcome"] == "ok"
        assert spans["xhuma.sds.response.decode"].attributes["stage.outcome"] == "ok"
        assert all(not span.events for span in spans.values())
        assert "SECRET_CACHE_CONNECTION_DETAILS" not in repr(spans)
    finally:
        provider.shutdown()


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
        with pytest.raises(HTTPException, match="SDS Device HTTP 503 failure") as caught:
            await sds_trace("A82038")
        assert caught.value.status_code == 503
        assert "Unavailable" in caught.value.detail
        cache.setex.assert_not_called()
        factory.return_value.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", [False, True], ids=["device", "endpoint"])
@pytest.mark.parametrize("status", [403, 429, 500, 503])
async def test_sds_http_failure_logs_status_and_request_ids_without_secrets(monkeypatch, caplog, endpoint, status):
    api_key = "non-uuid-nhs-api-credential"
    monkeypatch.setenv("NHS_API_KEY", api_key)
    upstream_request_id = "11111111-2222-4333-8444-555555555555"
    correlation_id = "66666666-7777-4888-8999-000000000000"
    body_secret_uuid = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    body = (
        f"SERVICE_UNAVAILABLE NHS 9690937278 Bearer sensitive.signed.token key={api_key} "
        f"secret={body_secret_uuid}\n" + "x" * 1000 + "END_OF_RESPONSE"
    )
    with (
        patch("app.pds.pds.redis_client", autospec=True) as cache,
        patch("app.pds.pds.httpx.AsyncClient") as factory,
    ):
        cache.get.return_value = None
        client = AsyncMock()
        client.get.return_value = httpx.Response(
            status,
            text=body,
            headers={"x-request-id": upstream_request_id, "x-correlation-id": correlation_id},
        )
        factory.return_value.__aenter__.return_value = client
        with pytest.raises(HTTPException) as caught:
            await sds_trace("H81058", endpoint=endpoint, mhsparty="H81058-825269")
        cache.setex.assert_not_called()
        sent_request_id = client.get.await_args.kwargs["headers"]["X-Request-ID"]

    resource = "Endpoint" if endpoint else "Device"
    records = [record for record in caplog.records if "SDS HTTP failure" in record.getMessage()]
    assert len(records) == 1
    message = records[0].getMessage()
    assert f"Resource: {resource} | ODS: H81058 | Status: {status}" in message
    assert f"ReqID: {sent_request_id}" in message
    assert f"UpstreamReqID: {upstream_request_id}" in message
    assert f"CorrID: {correlation_id}" in message
    assert "SERVICE_UNAVAILABLE" in message
    assert "[REDACTED_API_KEY]" in message
    assert "[REDACTED_NHSNO]" in message
    assert "[REDACTED_TOKEN]" in message
    assert len(message.split("Response: ", 1)[1]) <= 200
    assert "\n" not in message
    assert caught.value.status_code == status
    assert caught.value.detail.startswith(f"SDS {resource} HTTP {status} failure for ODS code H81058:")
    assert caught.value.detail.endswith(f"END_OF_RESPONSE (request_id: {sent_request_id})")
    assert "END_OF_RESPONSE" not in caplog.text
    for secret in (api_key, "9690937278", "sensitive.signed.token", body_secret_uuid):
        assert secret not in caplog.text
        assert secret not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type,status", [(httpx.ReadTimeout, 504), (httpx.ConnectError, 502)])
async def test_sds_transport_failure_has_caller_status_and_request_id(error_type, status, caplog):
    with (
        patch("app.pds.pds.redis_client", autospec=True) as cache,
        patch("app.pds.pds.httpx.AsyncClient") as factory,
    ):
        cache.get.return_value = None
        client = AsyncMock()
        client.get.side_effect = error_type("PRIVATE_CONNECTION_CREDENTIAL")
        factory.return_value.__aenter__.return_value = client
        with pytest.raises(HTTPException) as caught:
            await sds_trace("H81058", endpoint=True, mhsparty="H81058-825269")
        sent_request_id = client.get.await_args.kwargs["headers"]["X-Request-ID"]
        cache.setex.assert_not_called()
    assert caught.value.status_code == status
    assert error_type.__name__ in caught.value.detail
    assert sent_request_id in caught.value.detail
    assert "PRIVATE_CONNECTION_CREDENTIAL" not in str(caught.value)
    assert "PRIVATE_CONNECTION_CREDENTIAL" not in caplog.text


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
    monkeypatch.setenv("ORG_CODE", "RRV00")
    monkeypatch.setenv("ORG_ASID", "123")
    with pytest.raises(
        ValueError,
        match="XHUMA_ODS_CODE, ORG_CODE and ORG_ASID are required for the SDS self lookup",
    ):
        await lookup_self_device()


@pytest.mark.asyncio
async def test_lookup_self_device_missing_org_asid_raises(monkeypatch):
    from app.pds.pds import lookup_self_device

    monkeypatch.setenv("XHUMA_ODS_CODE", "Z6G1Z")
    monkeypatch.setenv("ORG_CODE", "RRV00")
    monkeypatch.delenv("ORG_ASID", raising=False)
    with pytest.raises(
        ValueError,
        match="XHUMA_ODS_CODE, ORG_CODE and ORG_ASID are required for the SDS self lookup",
    ):
        await lookup_self_device()


@pytest.mark.asyncio
async def test_lookup_self_device_success(monkeypatch):
    from app.pds.pds import lookup_self_device

    monkeypatch.setenv("XHUMA_ODS_CODE", "Z6G1Z")
    monkeypatch.setenv("ORG_CODE", "RRV00")
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
        mock_sds.assert_awaited_once_with("RRV00", manufacturer_ods="Z6G1Z")


def test_terraform_app_service_sds_self_identity_configured():
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    variables_tf = (repo_root / "infra" / "variables.tf").read_text()
    main_tf = (repo_root / "infra" / "main.tf").read_text()
    play_tfvars = (repo_root / "infra" / "env" / "play.tfvars").read_text()
    int_tfvars = (repo_root / "infra" / "env" / "int.tfvars").read_text()
    prd_tfvars = (repo_root / "infra" / "env" / "prd.tfvars").read_text()

    assert 'variable "xhuma_ods_code"' in variables_tf
    assert 'variable "xhuma_mhs_party_key"' in variables_tf
    assert '"XHUMA_ODS_CODE"' in main_tf and "var.xhuma_ods_code" in main_tf
    assert '"XHUMA_MHS_PARTY_KEY"' in main_tf and "var.xhuma_mhs_party_key" in main_tf

    # Play configuration (operates against INT Spine, aligned with INT consumer identity)
    assert 'xhuma_ods_code           = "RRV00"' in play_tfvars or 'xhuma_ods_code = "RRV00"' in play_tfvars
    assert (
        'xhuma_mhs_party_key      = "RRV00-824607"' in play_tfvars
        or 'xhuma_mhs_party_key = "RRV00-824607"' in play_tfvars
    )
    assert "Z6G1Z" not in play_tfvars

    # INT configuration
    assert "200000002574" in int_tfvars
    assert 'xhuma_ods_code      = "RRV00"' in int_tfvars or 'xhuma_ods_code = "RRV00"' in int_tfvars
    assert 'xhuma_mhs_party_key = "RRV00-824607"' in int_tfvars

    # PRD configuration
    assert "200000087786" in prd_tfvars
    assert 'xhuma_ods_code      = "Z6G1Z"' in prd_tfvars or 'xhuma_ods_code = "Z6G1Z"' in prd_tfvars
    assert 'xhuma_mhs_party_key = "Z6G1Z-838808"' in prd_tfvars

    # Ensure AUDIT_FAIL_OPEN remains unset in Terraform
    assert "AUDIT_FAIL_OPEN" not in main_tf


def test_play_sds_consumer_identity_matches_int():
    """Play operates against INT Spine, so its SDS consumer identity must match INT."""
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    play_tfvars = (repo_root / "infra" / "env" / "play.tfvars").read_text()
    int_tfvars = (repo_root / "infra" / "env" / "int.tfvars").read_text()

    def parse_tfvars(content: str) -> dict[str, str]:
        settings = {}
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.split("#")[0].strip().strip('"')
                settings[k] = v
        return settings

    play_config = parse_tfvars(play_tfvars)
    int_config = parse_tfvars(int_tfvars)

    assert play_config["env"] == "int"
    assert play_config["org_code"] == int_config["org_code"] == "RRV00"
    assert play_config["org_asid"] == int_config["org_asid"] == "200000002574"
    assert play_config["xhuma_ods_code"] == int_config["xhuma_ods_code"] == "RRV00"
    assert play_config["xhuma_mhs_party_key"] == int_config["xhuma_mhs_party_key"] == "RRV00-824607"


@pytest.mark.asyncio
async def test_lookup_self_device_with_party_key_success(monkeypatch):
    from app.pds.pds import lookup_self_device

    monkeypatch.setenv("XHUMA_ODS_CODE", "Z6G1Z")
    monkeypatch.setenv("ORG_CODE", "RRV00")
    monkeypatch.setenv("ORG_ASID", "200000087786")
    monkeypatch.setenv("XHUMA_MHS_PARTY_KEY", "Z6G1Z-838808")

    matching_device = {
        "resource": {
            "resourceType": "Device",
            "identifier": [
                {"system": "https://fhir.nhs.uk/Id/nhsSpineASID", "value": "200000087786"},
                {"system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey", "value": "Z6G1Z-838808"},
            ],
        }
    }
    other_device = {
        "resource": {
            "resourceType": "Device",
            "identifier": [
                {"system": "https://fhir.nhs.uk/Id/nhsSpineASID", "value": "999999999999"},
                {"system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey", "value": "RRV00-111111"},
            ],
        }
    }
    bundle = {
        "resourceType": "Bundle",
        "entry": [matching_device, other_device],
    }

    with patch("app.pds.pds.sds_trace", new_callable=AsyncMock) as mock_sds:
        mock_sds.return_value = bundle
        device = await lookup_self_device()
        assert device == matching_device
        mock_sds.assert_awaited_once_with("RRV00", manufacturer_ods="Z6G1Z")


@pytest.mark.asyncio
async def test_lookup_self_device_with_party_key_mismatch_rejected(monkeypatch):
    from app.pds.pds import lookup_self_device

    monkeypatch.setenv("XHUMA_ODS_CODE", "Z6G1Z")
    monkeypatch.setenv("ORG_CODE", "RRV00")
    monkeypatch.setenv("ORG_ASID", "200000087786")
    monkeypatch.setenv("XHUMA_MHS_PARTY_KEY", "Z6G1Z-838808")

    # Device has matching ASID but different party key
    wrong_party_device = {
        "resource": {
            "resourceType": "Device",
            "identifier": [
                {"system": "https://fhir.nhs.uk/Id/nhsSpineASID", "value": "200000087786"},
                {"system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey", "value": "Z6G1Z-WRONG"},
            ],
        }
    }
    bundle = {
        "resourceType": "Bundle",
        "entry": [wrong_party_device],
    }

    with patch("app.pds.pds.sds_trace", new_callable=AsyncMock) as mock_sds:
        mock_sds.return_value = bundle
        with pytest.raises(
            ValueError, match="SDS self lookup must return exactly one Device matching the configured Spine identity"
        ):
            await lookup_self_device()


@pytest.mark.asyncio
async def test_lookup_self_device_with_asid_mismatch_rejected(monkeypatch):
    from app.pds.pds import lookup_self_device

    monkeypatch.setenv("XHUMA_ODS_CODE", "Z6G1Z")
    monkeypatch.setenv("ORG_CODE", "RRV00")
    monkeypatch.setenv("ORG_ASID", "200000087786")
    monkeypatch.setenv("XHUMA_MHS_PARTY_KEY", "Z6G1Z-838808")

    # Device has matching party key but different ASID
    wrong_asid_device = {
        "resource": {
            "resourceType": "Device",
            "identifier": [
                {"system": "https://fhir.nhs.uk/Id/nhsSpineASID", "value": "999999999999"},
                {"system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey", "value": "Z6G1Z-838808"},
            ],
        }
    }
    bundle = {
        "resourceType": "Bundle",
        "entry": [wrong_asid_device],
    }

    with patch("app.pds.pds.sds_trace", new_callable=AsyncMock) as mock_sds:
        mock_sds.return_value = bundle
        with pytest.raises(
            ValueError, match="SDS self lookup must return exactly one Device matching the configured Spine identity"
        ):
            await lookup_self_device()


def test_terraform_app_service_nhs_api_key_configured():
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    main_tf = (repo_root / "infra" / "main.tf").read_text()
    assert (
        '"NHS_API_KEY"       = "@Microsoft.KeyVault(VaultName=${var.shared_key_vault_name};SecretName=nhs-api-key)"'
        in main_tf
        or '"NHS_API_KEY" = "@Microsoft.KeyVault(VaultName=${var.shared_key_vault_name};SecretName=nhs-api-key)"'
        in main_tf
    )


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

        # 1. 404 test
        resp_404 = MagicMock()
        resp_404.status_code = 404
        resp_404.json.return_value = {"issue": [{"diagnostics": "Not found"}]}
        client.get.return_value = resp_404

        with pytest.raises(fastapi.HTTPException) as excinfo:
            await lookup_patient(12345, saml=MagicMock())
        assert excinfo.value.status_code == 404
        assert excinfo.value.detail == "Patient not found on PDS"

        # 2. 401 test (Entitlement)
        resp_401 = MagicMock()
        resp_401.status_code = 401
        resp_401.json.return_value = {"issue": [{"diagnostics": "Unauthorised"}]}
        client.get.return_value = resp_401

        with pytest.raises(fastapi.HTTPException) as excinfo:
            await lookup_patient(12345, saml=MagicMock())
        assert excinfo.value.status_code == 502
        assert excinfo.value.detail == "PDS Entitlement or Token Failure"

        # 3. 403 test (Forbidden/Entitlement)
        resp_403 = MagicMock()
        resp_403.status_code = 403
        resp_403.json.return_value = {"issue": [{"diagnostics": "Forbidden"}]}
        client.get.return_value = resp_403

        with pytest.raises(fastapi.HTTPException) as excinfo:
            await lookup_patient(12345, saml=MagicMock())
        assert excinfo.value.status_code == 502
        assert excinfo.value.detail == "PDS Entitlement or Token Failure"

        # 4. upstream 500 test
        resp_500 = MagicMock()
        resp_500.status_code = 500
        resp_500.json.return_value = {"issue": [{"diagnostics": "Internal Server Error"}]}
        client.get.return_value = resp_500

        with pytest.raises(fastapi.HTTPException) as excinfo:
            await lookup_patient(12345, saml=MagicMock())
        assert excinfo.value.status_code == 502
        assert excinfo.value.detail == "PDS lookup upstream failure"

        # 5. upstream 503 test
        resp_503 = MagicMock()
        resp_503.status_code = 503
        resp_503.json.return_value = {"issue": [{"diagnostics": "Service Unavailable"}]}
        client.get.return_value = resp_503

        with pytest.raises(fastapi.HTTPException) as excinfo:
            await lookup_patient(12345, saml=MagicMock())
        assert excinfo.value.status_code == 502
        assert excinfo.value.detail == "PDS lookup upstream failure"

        # 6. Upstream Exception test (network failure)
        client.get.side_effect = Exception("Connection Refused")
        with pytest.raises(fastapi.HTTPException) as excinfo:
            await lookup_patient(12345, saml=MagicMock())
        assert excinfo.value.status_code == 502
        assert excinfo.value.detail == "PDS lookup upstream failure"


@pytest.mark.asyncio
async def test_pds_diagnostic_sanitization(caplog, monkeypatch):
    monkeypatch.setenv("NHS_API_KEY", "dummy")

    import fastapi

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

        # Response with sensitive looking text, UUID API key, and newlines
        sensitive_text = (
            "Detailed error: user token BEARER 12345-ABC \n and Patient NHSNO: 9999999999 and key "
            "a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d failed\r\n"
        )
        resp_error = MagicMock()
        resp_error.status_code = 500
        resp_error.json.return_value = {"issue": [{"diagnostics": sensitive_text}]}
        client.get.return_value = resp_error

        with pytest.raises(fastapi.HTTPException) as excinfo:
            await lookup_patient(12345, saml=MagicMock())
        assert excinfo.value.status_code == 502
        assert excinfo.value.detail == "PDS lookup upstream failure"

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
        assert "a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d" not in msg
        assert "[REDACTED_UUID]" in msg


@pytest.mark.asyncio
async def test_api_key_and_nhs_api_key_divergence_supported(monkeypatch):
    """Proves scenario 1: API_KEY != NHS_API_KEY is supported and expected."""
    monkeypatch.setenv("API_KEY", "xhuma-internal-secret")
    monkeypatch.setenv("NHS_API_KEY", "nhs-platform-credential")

    with (
        patch("app.pds.pds.redis_client", autospec=True) as cache,
        patch("app.pds.pds.attempt_audit", new_callable=AsyncMock),
        patch("app.pds.pds.pds_jwt", return_value="test-jwt") as mock_jwt,
        patch("app.pds.pds.httpx.AsyncClient") as factory,
    ):
        cache.exists.return_value = False
        cache.get.return_value = None

        client = AsyncMock()
        token_resp = MagicMock()
        token_resp.status_code = 200
        token_resp.json.return_value = {"access_token": "bearer-token", "expires_in": 300}
        client.post.return_value = token_resp

        patient_resp = MagicMock()
        patient_resp.status_code = 200
        patient_resp.json.return_value = {"resourceType": "Patient", "id": "9690937278"}
        client.get.return_value = patient_resp

        factory.return_value.__aenter__.return_value = client

        patient = await lookup_patient(9690937278, saml=saml)
        assert patient["id"] == "9690937278"

        # Verify pds_jwt was called with NHS_API_KEY, not API_KEY
        mock_jwt.assert_called_once()
        args, kwargs = mock_jwt.call_args
        assert args[0] == "nhs-platform-credential"  # iss
        assert args[1] == "nhs-platform-credential"  # sub
        assert "xhuma-internal-secret" not in args


@pytest.mark.asyncio
async def test_pds_oauth_uses_only_nhs_api_key(monkeypatch):
    """Proves scenario 2: PDS OAuth uses only NHS_API_KEY."""
    monkeypatch.setenv("API_KEY", "internal-xhuma-api-key")
    monkeypatch.setenv("NHS_API_KEY", "nhs-credential-xyz")

    with (
        patch("app.pds.pds.redis_client", autospec=True) as cache,
        patch("app.pds.pds.attempt_audit", new_callable=AsyncMock),
        patch("app.pds.pds.pds_jwt", return_value="test-assertion") as mock_jwt,
        patch("app.pds.pds.httpx.AsyncClient") as factory,
    ):
        cache.exists.return_value = False
        cache.get.return_value = None

        client = AsyncMock()
        client.post.return_value = MagicMock(status_code=200, json=lambda: {"access_token": "tok", "expires_in": 300})
        client.get.return_value = MagicMock(status_code=200, json=lambda: {"resourceType": "Patient", "id": "1"})
        factory.return_value.__aenter__.return_value = client

        await lookup_patient(1, saml=saml)

        # pds_jwt must be called with (nhs_api_key, nhs_api_key, full_path)
        assert mock_jwt.call_count == 1
        call_args = mock_jwt.call_args[0]
        assert call_args[0] == "nhs-credential-xyz"
        assert call_args[1] == "nhs-credential-xyz"
        assert call_args[0] != "internal-xhuma-api-key"


@pytest.mark.asyncio
async def test_sds_apikey_uses_only_nhs_api_key(monkeypatch):
    """Proves scenario 3: SDS apikey uses only NHS_API_KEY."""
    monkeypatch.setenv("API_KEY", "internal-xhuma-api-key")
    monkeypatch.setenv("NHS_API_KEY", "nhs-credential-xyz")

    with (
        patch("app.pds.pds.redis_client", autospec=True) as cache,
        patch("app.pds.pds.httpx.AsyncClient") as factory,
    ):
        cache.get.return_value = None

        client = AsyncMock()
        client.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"resourceType": "Bundle"},
            text='{"resourceType": "Bundle"}',
        )
        factory.return_value.__aenter__.return_value = client

        await sds_trace("A82038")

        client.get.assert_awaited_once()
        headers = client.get.await_args.kwargs["headers"]
        assert headers["apikey"] == "nhs-credential-xyz"
        assert headers["apikey"] != "internal-xhuma-api-key"


@pytest.mark.asyncio
async def test_missing_nhs_api_key_fails_closed(monkeypatch):
    """Proves scenario 4: Missing NHS_API_KEY fails closed in PDS and SDS."""
    monkeypatch.setenv("API_KEY", "internal-xhuma-api-key")
    monkeypatch.delenv("NHS_API_KEY", raising=False)

    import fastapi

    from app.audit.models import AuditOutcome

    with (
        patch("app.pds.pds.redis_client", autospec=True) as cache,
        patch("app.pds.pds.attempt_audit", new_callable=AsyncMock) as mock_audit,
    ):
        cache.get.return_value = None
        cache.exists.return_value = False

        # 1. SDS lookup fails closed without fallback to API_KEY
        with pytest.raises(ValueError, match="NHS_API_KEY environment variable is not set"):
            await sds_trace("A82038")

        # 2. PDS lookup fails closed without fallback to API_KEY
        with pytest.raises(fastapi.HTTPException) as excinfo:
            await lookup_patient(9690937278, saml=saml)
        assert excinfo.value.status_code == 502
        assert excinfo.value.detail == "Failed to obtain PDS token due to upstream error"

        # Verify audit recorded failure
        mock_audit.assert_called_once()
        assert mock_audit.call_args.kwargs["outcome"] == AuditOutcome.fail
        assert mock_audit.call_args.kwargs["detail"]["exception"] == "ValueError"


def test_api_key_remains_usable_for_xhuma_local_purposes(monkeypatch):
    """Proves scenario 5: API_KEY remains usable for its existing Xhuma-local purposes."""
    from app.pds.pds import pds_cache_key

    # When PDS_CACHE_HMAC_SECRET is unset, pds_cache_key falls back to API_KEY
    monkeypatch.delenv("PDS_CACHE_HMAC_SECRET", raising=False)
    monkeypatch.setattr("app.pds.pds.API_KEY", "xhuma-local-secret-1")
    key1 = pds_cache_key(9690937278)

    monkeypatch.setattr("app.pds.pds.API_KEY", "xhuma-local-secret-2")
    key2 = pds_cache_key(9690937278)

    # Different local API_KEY produces different HMAC cache keys
    assert key1 != key2

    # NHS_API_KEY changes have zero effect on local HMAC cache key
    monkeypatch.setenv("NHS_API_KEY", "some-unrelated-nhs-key")
    key2_again = pds_cache_key(9690937278)
    assert key2 == key2_again


@pytest.mark.asyncio
async def test_sds_trace_supports_manufacturing_organization(monkeypatch):
    """Consumer Device lookup must distinguish care org from supplier/manufacturer."""
    with (
        patch("app.pds.pds.redis_client", autospec=True) as cache,
        patch("app.pds.pds.httpx.AsyncClient") as factory,
    ):
        cache.get.return_value = None

        client = AsyncMock()
        client.get.return_value = httpx.Response(
            200,
            json={"resourceType": "Bundle", "total": 1},
        )
        factory.return_value.__aenter__.return_value = client

        await sds_trace("RRV00", manufacturer_ods="Z6G1Z")

        call = client.get.await_args
        assert call.kwargs["params"]["organization"] == ("https://fhir.nhs.uk/Id/ods-organization-code|RRV00")
        assert call.kwargs["params"]["manufacturing-organization"] == (
            "https://fhir.nhs.uk/Id/ods-organization-code|Z6G1Z"
        )
        assert any(
            value.endswith("|urn:nhs:names:services:gpconnect:fhir:operation:gpc.getstructuredrecord-1")
            for value in call.kwargs["params"]["identifier"]
        )
