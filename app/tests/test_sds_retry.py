"""Targeted tests for SDS transient failure handling, bounded retry, safe diagnostics, and security regressions."""

import json
import logging
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app import telemetry
from app.audit.models import AuditOutcome
from app.gpconnect import _fetch_gpconnect_record
from app.pds.pds import SDSUpstreamError, sds_cache_key, sds_trace
from app.tests.fixtures.saml_attributes import saml
from app.tests.test_gpconnect import fake_sds_endpoint_trace, get_mock_request, load_pds


@pytest.mark.asyncio
async def test_cache_miss_transient_503_then_200_succeeds_and_caches():
    """A. 503 -> 200: exactly two HTTP GETs, one 0.25s sleep, successful cache write."""
    success_bundle = {"resourceType": "Bundle", "id": "test-device-bundle"}
    with (
        patch("app.pds.pds.redis_client", autospec=True) as cache,
        patch("app.pds.pds.httpx.AsyncClient") as factory,
        patch("app.pds.pds.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        cache.get.return_value = None
        client = AsyncMock()
        resp_503 = httpx.Response(503, text="Service Unavailable")
        resp_200 = httpx.Response(200, json=success_bundle)
        client.get.side_effect = [resp_503, resp_200]
        factory.return_value.__aenter__.return_value = client

        result = await sds_trace("H81058")

        assert result == success_bundle
        assert client.get.await_count == 2
        mock_sleep.assert_awaited_once_with(0.25)

        # Fresh X-Request-ID on each attempt
        first_request_id = client.get.await_args_list[0].kwargs["headers"]["X-Request-ID"]
        second_request_id = client.get.await_args_list[1].kwargs["headers"]["X-Request-ID"]
        assert first_request_id != second_request_id

        # Result cached once with 12 hours TTL
        cache.setex.assert_awaited_once_with(
            sds_cache_key("H81058"),
            12 * 60 * 60,
            json.dumps(success_bundle),
        )


@pytest.mark.asyncio
async def test_cache_miss_two_transient_503_then_200_succeeds_and_caches():
    """B. 503 -> 503 -> 200: exactly 3 GETs, sleeps of 0.25 then 0.50, successful cache write once."""
    success_bundle = {"resourceType": "Bundle", "id": "test-device-bundle"}
    with (
        patch("app.pds.pds.redis_client", autospec=True) as cache,
        patch("app.pds.pds.httpx.AsyncClient") as factory,
        patch("app.pds.pds.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        cache.get.return_value = None
        client = AsyncMock()
        resp_503_1 = httpx.Response(503, text="Service Unavailable 1")
        resp_503_2 = httpx.Response(503, text="Service Unavailable 2")
        resp_200 = httpx.Response(200, json=success_bundle)
        client.get.side_effect = [resp_503_1, resp_503_2, resp_200]
        factory.return_value.__aenter__.return_value = client

        result = await sds_trace("H81058")

        assert result == success_bundle
        assert client.get.await_count == 3
        assert [call.args[0] for call in mock_sleep.await_args_list] == [0.25, 0.50]

        # Fresh X-Request-ID on each attempt
        request_ids = [call.kwargs["headers"]["X-Request-ID"] for call in client.get.await_args_list]
        assert len(set(request_ids)) == 3

        # Result cached once
        cache.setex.assert_awaited_once_with(
            sds_cache_key("H81058"),
            12 * 60 * 60,
            json.dumps(success_bundle),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 429])
async def test_deterministic_error_does_not_retry_or_cache(status_code):
    """F. Initial 403/429/etc: still exactly 1 GET, no sleep, no cache, typed safe failure."""
    with (
        patch("app.pds.pds.redis_client", autospec=True) as cache,
        patch("app.pds.pds.httpx.AsyncClient") as factory,
        patch("app.pds.pds.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        cache.get.return_value = None
        client = AsyncMock()
        client.get.return_value = httpx.Response(status_code, text="Forbidden or deterministic error")
        factory.return_value.__aenter__.return_value = client

        with pytest.raises(SDSUpstreamError) as exc_info:
            await sds_trace("H81058")

        err = exc_info.value
        assert err.status_code == status_code
        assert err.attempts == 1
        assert err.resource_type == "Device"
        assert err.reason == "upstream_http_failure"
        assert "Forbidden" not in str(err)
        assert client.get.await_count == 1
        mock_sleep.assert_not_called()
        cache.setex.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [408, 500, 502, 503, 504])
async def test_transient_error_retries_and_exhausts_bounded_attempts(status_code):
    """C. 503 -> 503 -> 503: exactly 3 GETs, sleeps of 0.25 then 0.50, no cache, status_code=503, attempts=3."""
    with (
        patch("app.pds.pds.redis_client", autospec=True) as cache,
        patch("app.pds.pds.httpx.AsyncClient") as factory,
        patch("app.pds.pds.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        cache.get.return_value = None
        client = AsyncMock()
        client.get.return_value = httpx.Response(status_code, text="Temporary error")
        factory.return_value.__aenter__.return_value = client

        with pytest.raises(SDSUpstreamError) as exc_info:
            await sds_trace("H81058", endpoint=True, mhsparty="PARTY01")

        err = exc_info.value
        assert err.status_code == status_code
        assert err.attempts == 3
        assert err.resource_type == "Endpoint"
        assert err.reason == "upstream_http_failure"
        assert "Temporary error" not in str(err)
        assert client.get.await_count == 3
        assert [call.args[0] for call in mock_sleep.await_args_list] == [0.25, 0.50]
        cache.setex.assert_not_called()


@pytest.mark.asyncio
async def test_transport_error_retries_and_succeeds_on_third_attempt():
    """D. transport error -> transport error -> 200: exactly 3 GETs, success and cache."""
    success_bundle = {"resourceType": "Bundle", "id": "test-endpoint-bundle"}
    with (
        patch("app.pds.pds.redis_client", autospec=True) as cache,
        patch("app.pds.pds.httpx.AsyncClient") as factory,
        patch("app.pds.pds.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        cache.get.return_value = None
        client = AsyncMock()
        resp_200 = httpx.Response(200, json=success_bundle)
        client.get.side_effect = [
            httpx.ReadTimeout("Connection timed out 1"),
            httpx.ConnectError("Connection failed 2"),
            resp_200,
        ]
        factory.return_value.__aenter__.return_value = client

        result = await sds_trace("H81058", endpoint=True, mhsparty="PARTY01")

        assert result == success_bundle
        assert client.get.await_count == 3
        assert [call.args[0] for call in mock_sleep.await_args_list] == [0.25, 0.50]
        cache.setex.assert_awaited_once_with(
            sds_cache_key("H81058", endpoint=True, partykey="PARTY01"),
            12 * 60 * 60,
            json.dumps(success_bundle),
        )


@pytest.mark.asyncio
async def test_transport_error_exhausts_bounded_attempts():
    """Transport error 3 times: exactly 3 GETs, sleeps of 0.25 then 0.50, no cache, status_code=None, attempts=3."""
    with (
        patch("app.pds.pds.redis_client", autospec=True) as cache,
        patch("app.pds.pds.httpx.AsyncClient") as factory,
        patch("app.pds.pds.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        cache.get.return_value = None
        client = AsyncMock()
        client.get.side_effect = httpx.ConnectError("Network unreachable")
        factory.return_value.__aenter__.return_value = client

        with pytest.raises(SDSUpstreamError) as exc_info:
            await sds_trace("H81058")

        err = exc_info.value
        assert err.status_code is None
        assert err.attempts == 3
        assert err.resource_type == "Device"
        assert err.reason == "transport_error"
        assert "Network unreachable" not in str(err)
        assert client.get.await_count == 3
        assert [call.args[0] for call in mock_sleep.await_args_list] == [0.25, 0.50]
        cache.setex.assert_not_called()


@pytest.mark.asyncio
async def test_transient_503_503_then_deterministic_403_fails_safely():
    """E. 503 -> 503 -> 403: exactly 3 GETs, stop on 403, sleeps of 0.25 then 0.50, no cache, status=403, attempts=3, no 4th attempt."""
    secret_body = "SECRET_403_DETAILS_INTERNAL"
    with (
        patch("app.pds.pds.redis_client", autospec=True) as cache,
        patch("app.pds.pds.httpx.AsyncClient") as factory,
        patch("app.pds.pds.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        cache.get.return_value = None
        client = AsyncMock()
        resp_503_1 = httpx.Response(503, text="Service Temporarily Unavailable 1")
        resp_503_2 = httpx.Response(503, text="Service Temporarily Unavailable 2")
        resp_403 = httpx.Response(403, text=f"Forbidden: {secret_body}")
        client.get.side_effect = [resp_503_1, resp_503_2, resp_403]
        factory.return_value.__aenter__.return_value = client

        with pytest.raises(SDSUpstreamError) as exc_info:
            await sds_trace("H81058")

        err = exc_info.value
        assert client.get.await_count == 3
        assert [call.args[0] for call in mock_sleep.await_args_list] == [0.25, 0.50]
        cache.setex.assert_not_called()
        assert err.status_code == 403
        assert err.attempts == 3
        assert err.resource_type == "Device"
        assert err.reason == "upstream_http_failure"
        assert secret_body not in str(err)
        assert secret_body not in repr(err)


@pytest.mark.asyncio
async def test_transient_503_then_deterministic_403_stops_immediately():
    """503 -> 403: exactly 2 GETs, stop on 403, single 0.25s sleep, no cache, status=403, attempts=2."""
    secret_body = "SECRET_403_STOP_EARLY"
    with (
        patch("app.pds.pds.redis_client", autospec=True) as cache,
        patch("app.pds.pds.httpx.AsyncClient") as factory,
        patch("app.pds.pds.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        cache.get.return_value = None
        client = AsyncMock()
        resp_503 = httpx.Response(503, text="Service Unavailable")
        resp_403 = httpx.Response(403, text=f"Forbidden: {secret_body}")
        client.get.side_effect = [resp_503, resp_403]
        factory.return_value.__aenter__.return_value = client

        with pytest.raises(SDSUpstreamError) as exc_info:
            await sds_trace("H81058")

        err = exc_info.value
        assert client.get.await_count == 2
        mock_sleep.assert_awaited_once_with(0.25)
        cache.setex.assert_not_called()
        assert err.status_code == 403
        assert err.attempts == 2
        assert err.resource_type == "Device"
        assert err.reason == "upstream_http_failure"
        assert secret_body not in str(err)
        assert secret_body not in repr(err)


@pytest.mark.asyncio
async def test_malformed_200_response_does_not_cache_or_retry():
    """Malformed 200 response: exactly 1 GET, do not accidentally cache it, do not turn it into a retry storm."""
    with (
        patch("app.pds.pds.redis_client", autospec=True) as cache,
        patch("app.pds.pds.httpx.AsyncClient") as factory,
        patch("app.pds.pds.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        cache.get.return_value = None
        client = AsyncMock()
        client.get.return_value = httpx.Response(200, text="NOT_VALID_JSON{foo:")
        factory.return_value.__aenter__.return_value = client

        with pytest.raises(SDSUpstreamError) as exc_info:
            await sds_trace("H81058")

        err = exc_info.value
        assert err.status_code == 200
        assert err.attempts == 1
        assert err.resource_type == "Device"
        assert err.reason == "malformed_json"
        assert "NOT_VALID_JSON" not in str(err)
        assert client.get.await_count == 1
        mock_sleep.assert_not_called()
        cache.setex.assert_not_called()


@pytest.mark.asyncio
async def test_security_regression_sentinel_secret_never_leaked(caplog):
    """Security regression: upstream response body containing sentinel secret must not appear in logs, audit, exception string, or telemetry."""
    sentinel_secret = "SENTINEL_VERY_SECRET_KEY_12345"

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    orig_tracer = telemetry.tracer
    telemetry.tracer = provider.get_tracer("test")

    try:
        with (
            patch("app.pds.pds.redis_client", autospec=True) as cache,
            patch("app.pds.pds.httpx.AsyncClient") as factory,
            patch("app.pds.pds.asyncio.sleep", new_callable=AsyncMock),
            caplog.at_level(logging.DEBUG),
        ):
            cache.get.return_value = None
            client = AsyncMock()
            client.get.return_value = httpx.Response(503, text=f"Downstream error with {sentinel_secret}")
            factory.return_value.__aenter__.return_value = client

            with pytest.raises(SDSUpstreamError) as exc_info:
                await sds_trace("H81058", endpoint=True, mhsparty="PARTY01")

            err = exc_info.value
            # 1. Exception string and repr
            assert sentinel_secret not in str(err)
            assert sentinel_secret not in repr(err)
            assert err.attempts == 3

            # 2. Logs
            assert sentinel_secret not in caplog.text

            # 3. Telemetry spans
            spans = exporter.get_finished_spans()
            http_spans = [s for s in spans if s.name.endswith("sds.http")]
            assert len(http_spans) == 3
            for span in http_spans:
                assert span.attributes["resource_type"] == "Endpoint"
                assert span.attributes["http_status_code"] == 503
                assert span.attributes["http_success"] is False

            for span in spans:
                assert sentinel_secret not in span.name
                for attr_key, attr_val in span.attributes.items():
                    assert sentinel_secret not in str(attr_key)
                    assert sentinel_secret not in str(attr_val)
                    # Verify no sensitive high-cardinality attributes
                    assert attr_key not in ["ods", "partykey", "url", "response_body"]
    finally:
        telemetry.tracer = orig_tracer
        provider.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "status", "reason", "attempts", "message"),
    [
        (httpx.ReadTimeout("PRIVATE_TRANSPORT_DETAIL"), 504, "transport_error", 3, "ReadTimeout"),
        (httpx.ConnectError("PRIVATE_TRANSPORT_DETAIL"), 502, "transport_error", 3, "ConnectError"),
        (httpx.Response(200, text="PRIVATE_MALFORMED_JSON"), 502, "malformed_json", 1, "malformed JSON"),
    ],
)
async def test_sds_transport_and_decode_failures_reach_gpconnect_caller(
    failure, status, reason, attempts, message, caplog
):
    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.pds.pds.redis_client", autospec=True) as cache,
        patch("app.pds.pds.httpx.AsyncClient") as factory,
        patch("app.pds.pds.asyncio.sleep", new_callable=AsyncMock),
        patch("app.audit.audit.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.logging.record_application_failure"),
    ):
        cache.get.return_value = None
        client = AsyncMock()
        if isinstance(failure, Exception):
            client.get.side_effect = failure
        else:
            client.get.return_value = failure
        factory.return_value.__aenter__.return_value = client
        response = await _fetch_gpconnect_record(9690937278, saml, request=get_mock_request())

    assert response.status_code == status
    body = json.loads(response.body)
    assert body["success"] is False
    assert message in body["error"]
    assert "request_id:" in body["error"]
    assert client.get.await_count == attempts
    client.post.assert_not_awaited()
    cache.setex.assert_not_called()
    assert audit.await_args.kwargs["error_code"] == str(status)
    assert audit.await_args.kwargs["detail"]["reason"] == reason
    assert audit.await_args.kwargs["detail"]["attempts"] == attempts
    assert "PRIVATE_" not in response.body.decode() + caplog.text + str(audit.await_args.kwargs)


@pytest.mark.asyncio
async def test_gpconnect_endpoint_sds_upstream_failure_audits_safe_fields(caplog):
    """GP Connect endpoint lookup failure with SDSUpstreamError records safe structured audit detail with 3 attempts."""
    sentinel = "UPSTREAM_SECRET_BODY_503"
    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect_stages.routing.sds_trace") as mock_sds,
        patch("app.audit.audit.attempt_audit", new_callable=AsyncMock) as mock_audit,
        patch("app.logging.record_application_failure"),
        caplog.at_level(logging.DEBUG),
    ):
        mock_sds.side_effect = SDSUpstreamError(
            status_code=503, attempts=3, resource_type="Endpoint", reason="upstream_http_failure"
        )

        response = await _fetch_gpconnect_record(9690937278, saml, request=get_mock_request())

        assert response.status_code == 503
        body = json.loads(response.body)
        assert body == {
            "success": False,
            "error": "SDS Endpoint resolution failed for ODS code E82665: "
            "SDS Endpoint upstream failure (status=503, attempts=3, reason=upstream_http_failure)",
        }
        assert sentinel not in response.body.decode()

        # Audit verification
        endpoint_audits = [
            call.kwargs for call in mock_audit.await_args_list if call.kwargs["action"] == "fhir_endpoint_trace"
        ]
        assert len(endpoint_audits) == 1
        audit_call = endpoint_audits[0]
        assert audit_call["outcome"] == AuditOutcome.fail
        assert audit_call["error_code"] == "503"
        assert audit_call["detail"] == {
            "step": "sds_endpoint.sds_lookup",
            "exception_type": "SDSUpstreamError",
            "reason": "upstream_http_failure",
            "upstream_status": 503,
            "attempts": 3,
        }
        assert sentinel not in str(audit_call)
        assert sentinel not in caplog.text
        assert "exception_type: SDSUpstreamError" in caplog.text
        assert "reason=upstream_http_failure, status=503, attempts=3" in caplog.text


@pytest.mark.asyncio
async def test_gpconnect_device_sds_upstream_failure_audits_safe_fields(caplog):
    """GP Connect device lookup failure with SDSUpstreamError records safe structured audit detail with 3 attempts."""
    sentinel = "UPSTREAM_SECRET_DEVICE_503"
    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect_stages.routing.sds_trace") as mock_sds,
        patch("app.audit.audit.attempt_audit", new_callable=AsyncMock) as mock_audit,
        patch("app.logging.record_application_failure"),
        caplog.at_level(logging.DEBUG),
    ):
        mock_sds.side_effect = [
            fake_sds_endpoint_trace(),
            SDSUpstreamError(status_code=503, attempts=3, resource_type="Device", reason="upstream_http_failure"),
        ]

        response = await _fetch_gpconnect_record(9690937278, saml, request=get_mock_request())

        assert response.status_code == 503
        body = json.loads(response.body)
        assert body == {
            "success": False,
            "error": "SDS Trace failed for ODS code E82665: "
            "SDS Device upstream failure (status=503, attempts=3, reason=upstream_http_failure)",
        }
        assert sentinel not in response.body.decode()

        device_audits = [call.kwargs for call in mock_audit.await_args_list if call.kwargs["action"] == "sds_trace"]
        assert len(device_audits) == 1
        audit_call = device_audits[0]
        assert audit_call["outcome"] == AuditOutcome.fail
        assert audit_call["error_code"] == "503"
        assert audit_call["detail"] == {
            "step": "sds_trace.sds_lookup",
            "exception_type": "SDSUpstreamError",
            "reason": "upstream_http_failure",
            "upstream_status": 503,
            "attempts": 3,
        }
        assert sentinel not in str(audit_call)
        assert sentinel not in caplog.text
        assert "exception_type: SDSUpstreamError" in caplog.text
        assert "reason=upstream_http_failure, status=503, attempts=3" in caplog.text
