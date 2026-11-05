import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import Response

from app.audit.audit import AuditFailureException
from app.audit.models import AuditOutcome
from app.gpconnect import gpconnect
from app.tests.configure_tests import load_pds
from app.tests.fixtures.saml_attributes import saml
from app.tests.gpconnect_helpers import fake_sds_device_trace, fake_sds_endpoint_trace, get_mock_request

pytest_plugins = ("pytest_asyncio",)
pytestmark = pytest.mark.usefixtures("mock_self_issuer")


@pytest.mark.asyncio
@pytest.mark.parametrize("audit_fails", [False, True])
async def test_self_lookup_failure_is_audited_without_downstream_request(mock_self_issuer, audit_fails):

    mock_self_issuer.side_effect = ValueError("SYNTHETIC_SENSITIVE_LOOKUP_ERROR")
    with (
        patch("app.gpconnect_stages.patient.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect_stages.routing.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.audit.audit.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.gpconnect_stages.transport.httpx.AsyncClient") as client,
        patch("app.gpconnect_stages.document.redis_client.pipeline") as cache,
    ):
        sds.side_effect = [fake_sds_endpoint_trace(), fake_sds_device_trace()]
        if audit_fails:

            async def fail_audit(**kwargs):
                if kwargs["action"] == "sds_self_lookup":
                    raise AuditFailureException("Audit unavailable")

            audit.side_effect = fail_audit
            with pytest.raises(AuditFailureException):
                await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())
        else:
            response = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())
            assert response.status_code == 502
            assert json.loads(response.body)["success"] is False
            assert json.loads(response.body)["error"].startswith("SDS consumer issuer lookup failed")
            assert "step:" in json.loads(response.body)["error"]
        assert audit.await_args.kwargs["action"] == "sds_self_lookup"
        assert audit.await_args.kwargs["outcome"] == AuditOutcome.fail
        assert audit.await_args.kwargs["detail"] == {
            "step": "sds_self_lookup.issuer_lookup",
            "exception_type": "ValueError",
        }
        client.assert_not_called()
        cache.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        "<html>Bad gateway</html>",
        "null",
        "[]",
        '{"resourceType":"Bundle"}',
        '{"resourceType":"OperationOutcome"}',
        '{"resourceType":"OperationOutcome","issue":[null]}',
        '{"resourceType":"OperationOutcome","issue":[{"severity":"error","code":"forbidden",'
        '"details":{"coding":[{"display":123}]}}]}',
    ],
)
async def test_gpconnect_malformed_error_retains_upstream_status(payload):
    with (
        patch("app.gpconnect.USE_RELAY", False),
        patch("app.gpconnect_stages.patient.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect_stages.routing.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.gpconnect_stages.transport.create_nhs_ssl_context"),
        patch("app.gpconnect_stages.transport.httpx.AsyncClient") as factory,
        patch("app.audit.audit.attempt_audit", new_callable=AsyncMock) as audit,
    ):
        sds.side_effect = [fake_sds_endpoint_trace(), fake_sds_device_trace()]
        factory.return_value.__aenter__.return_value.post = AsyncMock(return_value=Response(503, text=payload))
        response = await gpconnect(9690937278, saml, request=get_mock_request())

    assert response.status_code == 503
    assert json.loads(response.body) == {
        "success": False,
        "error": "Error from GP Connect endpoint 503",
        "reason": "unknown_provider_error",
        "http_status": 503,
    }
    assert audit.await_args.kwargs["error_code"] == "503"


@pytest.mark.asyncio
@patch("app.audit.audit.attempt_audit", new_callable=AsyncMock)
@patch("app.logging.record_application_failure")
@patch("app.gpconnect_stages.transport.httpx.AsyncClient")
@patch("app.gpconnect_stages.transport.create_nhs_ssl_context")
@patch("app.gpconnect_stages.routing.sds_trace", new_callable=AsyncMock)
@patch("app.gpconnect_stages.patient.lookup_patient", new_callable=AsyncMock)
async def test_gpconnect_readerror_audits_failure(
    mock_lookup_patient,
    mock_sds_trace,
    mock_create_nhs_ssl_context,
    mock_async_client,
    mock_record_application_failure,
    mock_attempt_audit,
):

    fake_pds = load_pds(9690937278)
    mock_lookup_patient.return_value = fake_pds
    mock_sds_trace.side_effect = [fake_sds_endpoint_trace(), fake_sds_device_trace()]

    mock_client = AsyncMock()
    mock_client.post.side_effect = httpx.ReadError("server closed connection before responding")
    mock_async_client.return_value.__aenter__.return_value = mock_client

    result = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())
    body = json.loads(result.body)

    assert result.status_code == 502
    assert body["success"] is False
    assert "ReadError: server closed connection before responding" in body["error"]

    assert mock_attempt_audit.call_count == 4
    audit_args = mock_attempt_audit.call_args.kwargs
    assert audit_args["action"] == "gpconnect_request"
    assert audit_args["error_code"] == "502"
    assert "exception" not in audit_args["detail"]
    assert audit_args["detail"]["exception_type"] == "ReadError"
    assert "transport" in audit_args["detail"]
    mock_record_application_failure.assert_called_once()


@pytest.mark.asyncio
@patch("app.audit.audit.attempt_audit", new_callable=AsyncMock)
@patch("app.gpconnect_stages.transport.httpx.AsyncClient")
@patch("app.gpconnect_stages.transport.create_nhs_ssl_context")
@patch("app.gpconnect_stages.routing.sds_trace", new_callable=AsyncMock)
@patch("app.gpconnect_stages.patient.lookup_patient", new_callable=AsyncMock)
async def test_gpconnect_non200_data_minimisation(
    mock_lookup_patient, mock_sds_trace, mock_create_nhs_ssl_context, mock_async_client, mock_attempt_audit
):

    fake_pds = load_pds(9690937278)
    mock_lookup_patient.return_value = fake_pds
    mock_sds_trace.side_effect = [fake_sds_endpoint_trace(), fake_sds_device_trace()]

    synthetic_body = "SYNTHETIC_DOWNSTREAM_BODY_MUST_NOT_BE_AUDITED"
    mock_client = AsyncMock()
    mock_client.post.return_value = Response(status_code=500, content=synthetic_body)
    mock_async_client.return_value.__aenter__.return_value = mock_client

    result = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())

    assert result.status_code == 500

    audit_calls = mock_attempt_audit.call_args_list
    gpconnect_request_call = [call for call in audit_calls if call.kwargs.get("action") == "gpconnect_request"]
    assert gpconnect_request_call

    audit_args = gpconnect_request_call[-1].kwargs
    assert audit_args["outcome"] == AuditOutcome.fail
    assert audit_args["error_code"] == "500"

    assert "response_text" not in audit_args["detail"]
    assert "transport" in audit_args["detail"]

    for arg in audit_args.values():
        assert synthetic_body not in str(arg)


@pytest.mark.asyncio
@patch("app.audit.audit.attempt_audit", new_callable=AsyncMock)
@patch("app.gpconnect_stages.transport.httpx.AsyncClient")
@patch("app.gpconnect_stages.transport.create_nhs_ssl_context")
@patch("app.gpconnect_stages.routing.sds_trace", new_callable=AsyncMock)
@patch("app.gpconnect_stages.patient.lookup_patient", new_callable=AsyncMock)
async def test_gpconnect_transport_exception_data_minimisation(
    mock_lookup_patient, mock_sds_trace, mock_create_nhs_ssl_context, mock_async_client, mock_attempt_audit
):

    fake_pds = load_pds(9690937278)
    mock_lookup_patient.return_value = fake_pds
    mock_sds_trace.side_effect = [fake_sds_endpoint_trace(), fake_sds_device_trace()]

    synthetic_body = "SYNTHETIC_SENSITIVE_EXCEPTION_TEXT"
    mock_client = AsyncMock()
    mock_client.post.side_effect = Exception(synthetic_body)
    mock_async_client.return_value.__aenter__.return_value = mock_client

    result = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())

    assert result.status_code == 502

    audit_calls = mock_attempt_audit.call_args_list
    gpconnect_request_call = [call for call in audit_calls if call.kwargs.get("action") == "gpconnect_request"]
    assert gpconnect_request_call

    audit_args = gpconnect_request_call[-1].kwargs
    assert audit_args["outcome"] == AuditOutcome.fail
    assert audit_args["error_code"] == "502"

    assert "exception" not in audit_args["detail"]
    assert audit_args["detail"]["exception_type"] == "Exception"
    assert "transport" in audit_args["detail"]

    for arg in audit_args.values():
        assert synthetic_body not in str(arg)
