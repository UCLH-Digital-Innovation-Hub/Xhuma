import base64
import importlib
import json
import logging
import xml.etree.ElementTree as ET
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import jwt
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from httpx import Response
from opentelemetry import trace
from opentelemetry.propagate import extract
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from starlette.requests import Request

from app.audit.audit import AuditFailureException
from app.audit.models import AuditOutcome, SAMLAttributes
from app.ccda.models.datatypes import CD
from app.gpconnect import _fetch_gpconnect_record, gpconnect
from app.tests.configure_tests import get_nhs_ids, load_bundle, load_pds
from app.tests.fixtures.saml_attributes import saml

pytest_plugins = ("pytest_asyncio",)
pytestmark = pytest.mark.usefixtures("mock_self_issuer")


def get_mock_request():
    mock_request = MagicMock()
    mock_request.app.state.SessionLocal = MagicMock()
    mock_request.app.state.ccda_expiry_hours = 4.0
    return mock_request


def fake_sds_device_trace():
    return {
        "resourceType": "Bundle",
        "entry": [
            {
                "resource": {
                    "resourceType": "Device",
                    "identifier": [
                        {
                            "system": "https://fhir.nhs.uk/Id/nhsSpineASID",
                            "value": "200000000000",
                        },
                        {
                            "system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey",
                            "value": "FAKE-PARTY-KEY",
                        },
                    ],
                }
            }
        ],
    }


def fake_sds_endpoint_trace():
    return {
        "resourceType": "Bundle",
        "entry": [
            {
                "resource": {
                    "resourceType": "Endpoint",
                    "address": "fake-gpconnect-endpoint",
                    "identifier": [
                        {
                            "system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey",
                            "value": "FAKE-PARTY-KEY",
                        }
                    ],
                }
            }
        ],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("nhsno", get_nhs_ids())
@patch("app.audit.audit.attempt_audit", new_callable=AsyncMock)
@patch("app.gpconnect_stages.document.convert_bundle", new_callable=AsyncMock)
@patch("app.gpconnect_stages.document.base64_xml")
@patch("app.gpconnect_stages.document.redis_client.pipeline")
@patch("app.gpconnect_stages.transport.create_nhs_ssl_context")
@patch("app.gpconnect_stages.transport.httpx.AsyncClient")
@patch("app.gpconnect.sds_trace", new_callable=AsyncMock)
@patch("app.gpconnect.lookup_patient", new_callable=AsyncMock)
async def test_gpconnect_with_nhs_data(
    mock_lookup_patient,
    mock_sds_trace,
    mock_async_client,
    mock_create_nhs_ssl_context,
    mock_redis_pipeline,
    mock_base64_xml,
    mock_convert_bundle,
    mock_attempt_audit,
    nhsno,
):
    pipeline = mock_redis_pipeline.return_value
    pipeline.__aenter__.return_value = pipeline
    pipeline.execute = AsyncMock()
    fake_bundle = load_bundle(nhsno)
    fake_pds = load_pds(nhsno)

    mock_lookup_patient.return_value = fake_pds

    # gpconnect calls sds_trace twice:
    # 1. Endpoint trace
    # 2. Device trace
    mock_sds_trace.side_effect = [
        fake_sds_endpoint_trace(),
        fake_sds_device_trace(),
    ]

    mock_response = Response(
        status_code=200,
        content=json.dumps(fake_bundle),
    )

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mock_async_client.return_value.__aenter__.return_value = mock_client

    mock_convert_bundle.return_value = {"ClinicalDocument": {"title": "Mocked CCDA document"}}

    mock_base64_xml.return_value = "mocked_base64_doc"

    result = await gpconnect(nhsno, saml_attrs=saml, request=get_mock_request())
    body = json.loads(result.body)

    assert result.status_code == 200
    assert body["success"] is True
    assert "document_id" in body

    mock_lookup_patient.assert_called_once_with(
        nhsno, request=mock_lookup_patient.call_args.kwargs["request"], saml=saml
    )
    assert mock_sds_trace.call_count == 2
    gp_ods = fake_pds["generalPractitioner"][0]["identifier"]["value"]
    assert mock_sds_trace.call_args_list[0].args == (gp_ods,)
    assert mock_sds_trace.call_args_list[0].kwargs == {"endpoint": True}
    assert mock_sds_trace.call_args_list[1].args == (gp_ods,)
    assert mock_sds_trace.call_args_list[1].kwargs == {"mhsparty": "FAKE-PARTY-KEY"}
    mock_client.post.assert_called_once()
    sent_headers = mock_client.post.call_args.kwargs["headers"]
    claims = jwt.decode(sent_headers["Authorization"].removeprefix("Bearer "), options={"verify_signature": False})
    assert claims["iss"] == "https://consumer.test/spine"
    assert claims["aud"] == "fake-gpconnect-endpoint"
    assert sent_headers["Ssp-From"] == "123"
    mock_convert_bundle.assert_called_once()
    mock_base64_xml.assert_called_once()
    document_key = f"document:{body['document_id']}"
    pipeline.setex.assert_called_once_with(str(nhsno), timedelta(hours=4), body["document_id"])
    pipeline.hset.assert_called_once_with(
        document_key, mapping={"nhs_number": str(nhsno), "content": "mocked_base64_doc"}
    )
    pipeline.expire.assert_called_once_with(document_key, timedelta(hours=4))
    mock_redis_pipeline.return_value.execute.assert_awaited_once()
    mock_redis_pipeline.return_value.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("audit_fails", [False, True])
async def test_self_lookup_failure_is_audited_without_downstream_request(mock_self_issuer, audit_fails):

    mock_self_issuer.side_effect = ValueError("SYNTHETIC_SENSITIVE_LOOKUP_ERROR")
    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
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
@patch("app.audit.audit.attempt_audit", new_callable=AsyncMock)
@patch("app.gpconnect.lookup_patient", new_callable=AsyncMock)
async def test_gpconnect_returns_400_for_invalid_nhs_number(mock_lookup_patient, mock_attempt_audit):
    result = await gpconnect(1234567890, saml_attrs=saml, request=get_mock_request())
    body = json.loads(result.body)

    assert result.status_code == 400
    assert body["success"] is False
    assert "not a valid NHS number" in body["error"]

    mock_lookup_patient.assert_not_called()


@pytest.mark.asyncio
@patch("app.audit.audit.attempt_audit", new_callable=AsyncMock)
@patch("app.gpconnect.lookup_patient", new_callable=AsyncMock)
async def test_gpconnect_returns_502_when_pds_lookup_fails(mock_lookup_patient, mock_attempt_audit):
    mock_lookup_patient.side_effect = Exception("PDS unavailable")

    result = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())
    body = json.loads(result.body)

    assert result.status_code == 502
    assert body["success"] is False
    assert "PDS lookup failed" in body["error"]


@pytest.mark.asyncio
@patch("app.audit.audit.attempt_audit", new_callable=AsyncMock)
@patch("app.gpconnect.lookup_patient", new_callable=AsyncMock)
async def test_gpconnect_returns_403_when_patient_restricted(mock_lookup_patient, mock_attempt_audit):
    fake_pds = load_pds(9690937278)
    fake_pds["meta"]["security"][0]["code"] = "R"

    mock_lookup_patient.return_value = fake_pds

    result = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())
    body = json.loads(result.body)

    assert result.status_code == 403
    assert body["success"] is False
    assert "not unrestricted" in body["error"]


@pytest.mark.asyncio
@patch("app.audit.audit.attempt_audit", new_callable=AsyncMock)
@patch("app.gpconnect.sds_trace", new_callable=AsyncMock)
@patch("app.gpconnect.lookup_patient", new_callable=AsyncMock)
async def test_gpconnect_returns_502_when_sds_trace_fails(
    mock_lookup_patient,
    mock_sds_trace,
    mock_attempt_audit,
):
    fake_pds = load_pds(9690937278)

    mock_lookup_patient.return_value = fake_pds
    mock_sds_trace.side_effect = Exception("SDS unavailable")

    result = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())
    body = json.loads(result.body)

    assert result.status_code == 502
    assert body["success"] is False
    assert body["error"].startswith("SDS Endpoint resolution failed for ODS code E82665")
    assert "step:" in body["error"]
    assert "SDS unavailable" not in body["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("audit_fails", [False, True])
@patch("app.audit.audit.attempt_audit", new_callable=AsyncMock)
@patch("app.gpconnect.sds_trace", new_callable=AsyncMock)
@patch("app.gpconnect.lookup_patient", new_callable=AsyncMock)
@patch("app.logging.record_application_failure")
async def test_gpconnect_zero_entries_sds_endpoint_bundle(
    mock_record_failure,
    mock_lookup_patient,
    mock_sds_trace,
    mock_attempt_audit,
    audit_fails,
):
    fake_pds = load_pds(9690937278)
    fake_pds["generalPractitioner"][0]["identifier"]["value"] = "W93019"
    mock_lookup_patient.return_value = fake_pds
    mock_sds_trace.return_value = {"resourceType": "Bundle", "type": "searchset", "total": 0, "entry": []}

    if audit_fails:

        async def fail_on_fhir_endpoint_trace(*args, **kwargs):
            if kwargs.get("action") == "fhir_endpoint_trace":
                raise AuditFailureException("Audit persistence failed")
            return None

        mock_attempt_audit.side_effect = fail_on_fhir_endpoint_trace
        with pytest.raises(AuditFailureException):
            await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())
    else:
        result = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())
        body = json.loads(result.body)

        assert result.status_code == 502
        assert body["success"] is False
        assert "Unable to resolve GP Connect Structured Record FHIR endpoint in SDS for ODS code" in body["error"]
        assert "W93019" in body["error"]
        assert "malformed" not in body["error"]
        assert "Unable to parse" not in body["error"]
        mock_record_failure.assert_not_called()

        audits = [
            call.kwargs for call in mock_attempt_audit.await_args_list if call.kwargs["action"] == "fhir_endpoint_trace"
        ]
        assert len(audits) == 1
        assert audits[0]["outcome"] == AuditOutcome.fail
        assert audits[0]["error_code"] == "502"
        assert audits[0]["detail"]["reason"] == "no_gp_connect_structured_record_route"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 404, 429, 500, 503])
@pytest.mark.parametrize("failed_lookup", ["device", "endpoint", "self"])
@pytest.mark.parametrize("wire", ["json", "soap"])
async def test_sds_http_failure_reaches_caller(mock_self_issuer, monkeypatch, status, failed_lookup, wire):
    pds = importlib.import_module("app.pds.pds")
    iti38 = importlib.import_module("app.soap.responses.iti_38")
    api_key = "private-nhs-api-credential"
    monkeypatch.setenv("NHS_API_KEY", api_key)
    monkeypatch.setenv("XHUMA_ODS_CODE", "Z6G1Z")
    if failed_lookup == "self":
        mock_self_issuer.side_effect = pds.lookup_self_issuer
    patient = load_pds(9690937278)
    patient["generalPractitioner"][0]["identifier"]["value"] = "H81058"
    responses = []
    if failed_lookup != "endpoint":
        responses.append(Response(200, json=fake_sds_endpoint_trace()))
    if failed_lookup == "self":
        responses.append(Response(200, json=fake_sds_device_trace()))
    provider_detail = "endpoint <unavailable> & retry " + "x" * 256 + "SDS_PROVIDER_DIAGNOSTIC_END"
    responses.append(
        Response(
            status,
            json={
                "error": "SDS_PROVIDER_UNAVAILABLE",
                "message": api_key,
                "diagnostics": provider_detail,
            },
        )
    )
    successful_lookups = len(responses) - 1
    attempts = 3 if status in pds.SDS_TRANSIENT_STATUS_CODES else 1
    responses.extend([responses[-1]] * (attempts - 1))
    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=patient),
        patch("app.pds.pds.redis_client", autospec=True) as cache,
        patch("app.pds.pds.httpx.AsyncClient") as factory,
        patch("app.pds.pds.asyncio.sleep", new_callable=AsyncMock),
        patch("app.audit.audit.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.soap.responses.iti_38.redis_client", autospec=True) as soap_cache,
        patch("app.soap.responses.iti_38.attempt_audit", new_callable=AsyncMock),
    ):
        cache.get.return_value = None
        soap_cache.get.return_value = None
        client = AsyncMock()
        client.get.side_effect = responses
        factory.return_value.__aenter__.return_value = client
        if wire == "json":
            response = await gpconnect(9690937278, saml, request=get_mock_request())
            assert response.status_code == status
            body = json.loads(response.body)
            assert body["success"] is False
            error = body["error"]
        else:
            xml = await iti38.iti_38_response(get_mock_request(), 9690937278, "ceid", "queryid", saml)
            error_node = ET.fromstring(xml).find(".//{urn:oasis:names:tc:ebxml-regrep:xsd:rs:3.0}RegistryError")
            assert error_node is not None
            assert error_node.attrib["errorCode"] == "XDSRegistryError"
            error = error_node.attrib["codeContext"]
        assert f"HTTP {status}" in error
        assert "SDS_PROVIDER_UNAVAILABLE" in error
        assert provider_detail in error
        assert "request_id:" in error
        assert api_key not in error
        assert audit.await_args.kwargs["error_code"] == str(status)
        assert "SDS_PROVIDER_UNAVAILABLE" not in str(audit.await_args.kwargs)
        client.post.assert_not_awaited()
        # Only the successful preceding lookups are cached, never the failed response.
        assert cache.setex.await_count == successful_lookups
        assert client.get.await_count == successful_lookups + attempts
        assert audit.await_args.kwargs["detail"]["attempts"] == attempts


@pytest.mark.asyncio
@pytest.mark.parametrize("wire", ["json", "soap"])
@pytest.mark.parametrize(
    ("status", "displays", "expected"),
    [
        (403, ["Patient has not provided consent to share data"], "Patient has not provided consent to share data"),
        (403, ["ASID_CHECK_FAILED_MESSAGESENDER_100000000001"], "ASID_CHECK_FAILED_MESSAGESENDER_100000000001"),
        (404, ["Patient not found"], "Patient not found"),
        (500, ["Internal server error"], "Internal server error"),
        (
            422,
            ["Invalid parameter", "Reference not found", "Invalid parameter"],
            "Invalid parameter | Reference not found",
        ),
        (403, [" Access\ndenied\r\n"], "Access denied"),
        (403, ["", "  "], ""),
    ],
)
async def test_gpconnect_error_display_reaches_caller_and_logs(status, displays, expected, wire, tmp_path, caplog):
    payload = {
        "resourceType": "OperationOutcome",
        "issue": [
            {
                "severity": "error",
                "code": "processing",
                "details": {"coding": [{"code": "PROVIDER_ERROR_CODE", "display": display}]},
                "diagnostics": "PRIVATE_PROVIDER_DIAGNOSTICS",
            }
            for display in displays
        ],
    }
    with (
        patch("app.gpconnect.USE_RELAY", False),
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.gpconnect_stages.transport.create_nhs_ssl_context"),
        patch("app.gpconnect_stages.transport.httpx.AsyncClient") as factory,
        patch("app.audit.audit.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.logging.record_application_failure") as telemetry,
        patch("app.soap.responses.iti_38.redis_client", autospec=True) as soap_cache,
        patch("app.soap.responses.iti_38.attempt_audit", new_callable=AsyncMock),
    ):
        sds.side_effect = [fake_sds_endpoint_trace(), fake_sds_device_trace()]
        factory.return_value.__aenter__.return_value.post = AsyncMock(return_value=Response(status, json=payload))
        soap_cache.get.return_value = None
        if wire == "json":
            response = await _fetch_gpconnect_record(
                9690937278, saml, log_dir=str(tmp_path), request=get_mock_request()
            )
            assert response.status_code == status
            body = json.loads(response.body)
            assert set(body) == {"success", "error"}
            assert body["success"] is False
            error = body["error"]
        else:
            iti38 = importlib.import_module("app.soap.responses.iti_38")
            xml = await iti38.iti_38_response(get_mock_request(), 9690937278, "ceid", "queryid", saml)
            error_node = ET.fromstring(xml).find(".//{urn:oasis:names:tc:ebxml-regrep:xsd:rs:3.0}RegistryError")
            assert error_node is not None
            assert error_node.attrib["errorCode"] == "XDSRegistryError"
            error = error_node.attrib["codeContext"]

    msg = f"Error from GP Connect endpoint {status}" + (f": {expected}" if expected else "")
    assert error == msg
    diagnostic = f"{msg} (step: gpconnect_request.response_status)"
    assert diagnostic in caplog.text
    telemetry.assert_called_once()
    assert str(telemetry.call_args.args[0]) == diagnostic
    if wire == "json":
        assert (tmp_path / "error.log").read_text() == diagnostic + "\n"
    assert "PRIVATE_PROVIDER_DIAGNOSTICS" not in error + caplog.text
    assert "PROVIDER_ERROR_CODE" not in error + caplog.text
    assert audit.await_args.kwargs["error_code"] == str(status)
    assert audit.await_args.kwargs["outcome"] == AuditOutcome.fail
    assert audit.await_args.kwargs["detail"]["step"] == "gpconnect_request.response_status"


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
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.gpconnect_stages.transport.create_nhs_ssl_context"),
        patch("app.gpconnect_stages.transport.httpx.AsyncClient") as factory,
        patch("app.audit.audit.attempt_audit", new_callable=AsyncMock) as audit,
    ):
        sds.side_effect = [fake_sds_endpoint_trace(), fake_sds_device_trace()]
        factory.return_value.__aenter__.return_value.post = AsyncMock(return_value=Response(503, text=payload))
        response = await gpconnect(9690937278, saml, request=get_mock_request())

    assert response.status_code == 503
    assert json.loads(response.body) == {"success": False, "error": "Error from GP Connect endpoint 503"}
    assert audit.await_args.kwargs["error_code"] == "503"


@pytest.mark.asyncio
async def test_serialization_failure_returns_caller_error_with_step():
    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.audit.audit.attempt_audit", new_callable=AsyncMock),
        patch("app.gpconnect_stages.transport.create_nhs_ssl_context"),
        patch("app.gpconnect_stages.transport.httpx.AsyncClient") as factory,
        patch(
            "app.gpconnect_stages.document.convert_bundle",
            new_callable=AsyncMock,
            return_value={"ClinicalDocument": {}},
        ),
        patch("app.gpconnect_stages.document.base64_xml", side_effect=ValueError("PRIVATE_INTERNAL_ERROR")),
    ):
        sds.side_effect = [fake_sds_endpoint_trace(), fake_sds_device_trace()]
        factory.return_value.__aenter__.return_value.post.return_value = Response(
            200, json={"resourceType": "Bundle", "type": "searchset", "entry": []}
        )
        response = await gpconnect(9690937278, saml, request=get_mock_request())
    assert response.status_code == 500
    body = json.loads(response.body)
    assert body["success"] is False
    assert "ccda_serialize.base64_xml" in body["error"]
    assert "ValueError" in body["error"]
    assert "PRIVATE_INTERNAL_ERROR" not in body["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("device_trace", "exception_type"),
    [
        pytest.param({"resourceType": "Bundle", "entry": [{"resource": None}]}, "AttributeError", id="null-resource"),
        pytest.param(
            {"resourceType": "Bundle", "entry": [{"resource": {"identifier": None}}]},
            "TypeError",
            id="null-identifiers",
        ),
        pytest.param("sensitive-identifier", "RuntimeError", id="sensitive-identifier"),
    ],
)
async def test_device_parse_failure_sanitises_output(mock_self_issuer, tmp_path, caplog, device_trace, exception_type):
    upstream_secret = "SECRET_UPSTREAM_BODY"
    upstream_error = RuntimeError(f"Corrupt Device identifier: {upstream_secret}")

    class SensitiveIdentifier(dict):
        def get(self, key, default=None):
            raise upstream_error

    if device_trace == "sensitive-identifier":
        device_trace = {
            "resourceType": "Bundle",
            "entry": [
                {
                    "resource": {
                        "resourceType": "Device",
                        "identifier": [SensitiveIdentifier()],
                    }
                }
            ],
        }

    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.audit.audit.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.logging.record_application_failure") as telemetry,
        patch("app.gpconnect_stages.transport.httpx.AsyncClient") as client,
    ):
        sds.side_effect = [fake_sds_endpoint_trace(), device_trace]
        response = await _fetch_gpconnect_record(9690937278, saml, log_dir=str(tmp_path), request=get_mock_request())

    safe_message = "Unable to parse SDS trace response"
    assert response.status_code == 500
    assert json.loads(response.body)["success"] is False
    assert json.loads(response.body)["error"].startswith(safe_message)
    assert "step:" in json.loads(response.body)["error"]
    assert upstream_secret not in response.body.decode()

    telemetry.assert_called_once()
    recorded_error = telemetry.call_args.args[0]
    assert isinstance(recorded_error, RuntimeError)
    step = "device_extraction" if exception_type in ("IndexError", "AttributeError") else "identifier_extraction"
    diagnostic = f"{safe_message} (step: resolve_asid_partykey.{step}, exception_type: {exception_type})"
    assert str(recorded_error) == diagnostic
    assert upstream_secret not in repr(recorded_error)
    assert recorded_error is not upstream_error

    assert (tmp_path / "error.log").read_text() == diagnostic + "\n"
    assert diagnostic in caplog.text
    assert upstream_secret not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)
    assert all(upstream_secret not in str(call.kwargs) for call in audit.await_args_list)
    assert sds.await_count == 2
    assert any(
        call.kwargs["action"] == "fhir_endpoint_trace" and call.kwargs["outcome"] == AuditOutcome.ok
        for call in audit.await_args_list
    )
    assert any(
        call.kwargs["action"] == "resolve_asid_partykey" and call.kwargs["outcome"] == AuditOutcome.fail
        for call in audit.await_args_list
    )
    mock_self_issuer.assert_not_awaited()
    client.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("wire", ["json", "soap"])
@pytest.mark.parametrize(
    ("endpoint_trace", "exception_type"),
    [
        pytest.param(None, "HTTPException", id="null-response"),
        pytest.param({}, "HTTPException", id="missing-entry"),
        pytest.param({"resourceType": "Bundle", "entry": None}, "HTTPException", id="null-entry"),
        pytest.param({"entry": []}, "HTTPException", id="empty-entry"),
        pytest.param({"resourceType": "Bundle", "entry": [None]}, "TypeError", id="null-first-entry"),
        pytest.param({"resourceType": "Bundle", "entry": [{}]}, "KeyError", id="missing-resource"),
        pytest.param({"resourceType": "Bundle", "entry": [{"resource": None}]}, "TypeError", id="null-resource"),
        pytest.param({"resourceType": "Bundle", "entry": [{"resource": {}}]}, "KeyError", id="missing-address"),
        pytest.param(
            {"resourceType": "Bundle", "entry": [{"resource": {"address": None}}]}, "ValueError", id="null-address"
        ),
        pytest.param(
            {"resourceType": "Bundle", "entry": [{"resource": {"address": ""}}]}, "ValueError", id="empty-address"
        ),
        pytest.param(
            {"resourceType": "Bundle", "entry": [{"resource": {"address": " \t\n"}}]}, "ValueError", id="blank-address"
        ),
        pytest.param(
            {"resourceType": "Bundle", "entry": [{"resource": {"address": 123}}]}, "ValueError", id="nonstring-address"
        ),
    ],
)
async def test_invalid_endpoint_trace_is_audited_without_downstream_request(
    mock_self_issuer, endpoint_trace, exception_type, wire
):

    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.audit.audit.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.gpconnect_stages.transport.httpx.AsyncClient") as client,
        patch("app.soap.responses.iti_38.redis_client", autospec=True) as soap_cache,
        patch("app.soap.responses.iti_38.attempt_audit", new_callable=AsyncMock),
    ):
        sds.side_effect = [endpoint_trace]
        soap_cache.get.return_value = None
        if wire == "json":
            response = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())
            assert response.status_code == 502
            body = json.loads(response.body)
            assert body["success"] is False
            error = body["error"]
        else:
            iti38 = importlib.import_module("app.soap.responses.iti_38")
            xml = await iti38.iti_38_response(get_mock_request(), 9690937278, "ceid", "queryid", saml)
            error_node = ET.fromstring(xml).find(".//{urn:oasis:names:tc:ebxml-regrep:xsd:rs:3.0}RegistryError")
            assert error_node is not None
            assert error_node.attrib["errorCode"] == "XDSRegistryError"
            error = error_node.attrib["codeContext"]

        assert error.startswith("SDS Endpoint resolution failed for ODS code E82665")
        if exception_type == "HTTPException":
            step = "response_validation"
            assert "SDS Endpoint lookup returned no entries for ODS code E82665" in error
        else:
            step = "address_validation" if exception_type == "ValueError" else "address_extraction"
            assert "step:" in error
        endpoint_audits = [
            call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "fhir_endpoint_trace"
        ]
        assert len(endpoint_audits) == 1
        assert endpoint_audits[0]["outcome"] == AuditOutcome.fail
        assert endpoint_audits[0]["error_code"] == "502"
        assert endpoint_audits[0]["detail"] == {
            "step": "sds_endpoint." + step,
            "exception_type": exception_type,
        }
        sds.assert_awaited_once()
        assert not any(call.kwargs["action"] == "sds_trace" for call in audit.await_args_list)
        mock_self_issuer.assert_not_awaited()
        client.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("step", ["success_audit", "audit_failure"])
async def test_endpoint_audit_failures_are_distinguished(mock_self_issuer, step, caplog):
    secret = "SECRET_UPSTREAM_BODY"

    async def fail_audit(**kwargs):
        if kwargs["action"] == "fhir_endpoint_trace" and kwargs["outcome"] == AuditOutcome.ok:
            if step == "success_audit":
                raise RuntimeError(secret)
            if step == "audit_failure":
                raise AuditFailureException("Audit unavailable")

    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.audit.audit.attempt_audit", new_callable=AsyncMock, side_effect=fail_audit) as audit,
        patch("app.logging.record_application_failure") as telemetry,
        patch("app.gpconnect_stages.transport.httpx.AsyncClient") as client,
    ):
        sds.side_effect = [fake_sds_endpoint_trace()]
        if step == "audit_failure":
            with pytest.raises(AuditFailureException):
                await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())
            telemetry.assert_called_once()
            diagnostic = str(telemetry.call_args.args[0])
            assert "step: sds_endpoint.success_audit, exception_type: AuditFailureException" in diagnostic
            assert not any(call.kwargs["outcome"] == AuditOutcome.fail for call in audit.await_args_list)
        else:
            response = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())
            assert response.status_code == 502
            assert json.loads(response.body)["error"].startswith("SDS Endpoint resolution failed for ODS code E82665")
            assert "step:" in json.loads(response.body)["error"]
            assert audit.await_args.kwargs["detail"] == {
                "step": "sds_endpoint." + step,
                "exception_type": "RuntimeError",
            }
            diagnostic = str(telemetry.call_args.args[0])
            assert f"step: sds_endpoint.{step}" in diagnostic
            assert "exception_type: RuntimeError" in diagnostic
            assert secret not in diagnostic
            assert f"step: sds_endpoint.{step}" in caplog.text
        assert secret not in caplog.text
        assert sds.await_count == 1
        assert not any(call.kwargs["action"] == "sds_trace" for call in audit.await_args_list)
        mock_self_issuer.assert_not_awaited()
        client.assert_not_called()


@pytest.mark.asyncio
async def test_multiple_endpoint_entries_fail_closed(mock_self_issuer, caplog):
    first_address = "https://first-gpconnect-endpoint.example/fhir"
    second_address = "https://second-gpconnect-endpoint.example/fhir"
    party_key_1 = "PARTY-KEY-SECRET-1"
    party_key_2 = "PARTY-KEY-SECRET-2"
    nhs_no = 9690937278
    endpoint_trace = {
        "resourceType": "Bundle",
        "entry": [
            {
                "resource": {
                    "resourceType": "Endpoint",
                    "address": first_address,
                    "identifier": [
                        {
                            "system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey",
                            "value": party_key_1,
                        }
                    ],
                }
            },
            {
                "resource": {
                    "resourceType": "Endpoint",
                    "address": second_address,
                    "identifier": [
                        {
                            "system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey",
                            "value": party_key_2,
                        }
                    ],
                }
            },
        ],
    }

    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(nhs_no)),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.audit.audit.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.logging.record_application_failure") as telemetry,
        patch("app.gpconnect_stages.transport.httpx.AsyncClient") as client,
        caplog.at_level(logging.DEBUG),
    ):
        sds.side_effect = [endpoint_trace]

        response = await gpconnect(nhs_no, saml_attrs=saml, request=get_mock_request())

    assert response.status_code == 502
    body = json.loads(response.body)
    assert body["success"] is False
    assert body["error"] == "Ambiguous SDS Endpoint response for ODS code E82665"

    assert sds.call_count == 1
    endpoint_audits = [call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "fhir_endpoint_trace"]
    assert len(endpoint_audits) == 1
    assert endpoint_audits[0]["outcome"] == AuditOutcome.fail
    assert endpoint_audits[0]["error_code"] == "502"
    assert endpoint_audits[0]["detail"]["reason"] == "ambiguous_endpoint_entries"

    assert not any(call.kwargs["action"] == "sds_trace" for call in audit.await_args_list)
    assert not any(call.kwargs["action"] == "resolve_asid_partykey" for call in audit.await_args_list)
    mock_self_issuer.assert_not_awaited()
    client.assert_not_called()

    telemetry.assert_called_once()
    diagnostic = str(telemetry.call_args.args[0])
    assert "Ambiguous SDS Endpoint response for ODS code E82665" in diagnostic

    for sensitive in (first_address, second_address, party_key_1, party_key_2, str(nhs_no)):
        assert sensitive not in diagnostic
        assert sensitive not in response.body.decode()
        assert sensitive not in caplog.text
        assert sensitive not in str(endpoint_audits[0]["detail"])


@pytest.mark.asyncio
async def test_multiple_device_entries_fail_closed(mock_self_issuer, caplog):
    party_key = "FAKE-PARTY-KEY-99"
    asid_1 = "200000000001"
    asid_2 = "200000000002"
    nhs_no = 9690937278
    endpoint_trace = {
        "resourceType": "Bundle",
        "entry": [
            {
                "resource": {
                    "resourceType": "Endpoint",
                    "address": "https://gpconnect.example/fhir",
                    "identifier": [
                        {
                            "system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey",
                            "value": party_key,
                        }
                    ],
                }
            }
        ],
    }
    device_bundle = {
        "resourceType": "Bundle",
        "entry": [
            {
                "resource": {
                    "resourceType": "Device",
                    "identifier": [
                        {
                            "system": "https://fhir.nhs.uk/Id/nhsSpineASID",
                            "value": asid_1,
                        }
                    ],
                }
            },
            {
                "resource": {
                    "resourceType": "Device",
                    "identifier": [
                        {
                            "system": "https://fhir.nhs.uk/Id/nhsSpineASID",
                            "value": asid_2,
                        }
                    ],
                }
            },
        ],
    }

    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(nhs_no)),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.audit.audit.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.logging.record_application_failure") as telemetry,
        patch("app.gpconnect_stages.transport.httpx.AsyncClient") as client,
        caplog.at_level(logging.DEBUG),
    ):
        sds.side_effect = [endpoint_trace, device_bundle]

        response = await gpconnect(nhs_no, saml_attrs=saml, request=get_mock_request())

    assert response.status_code == 500
    body = json.loads(response.body)
    assert body["success"] is False
    assert body["error"] == "Ambiguous SDS Device response for ODS code E82665"

    assert sds.call_count == 2
    assert sds.call_args_list[0].kwargs == {"endpoint": True}
    assert sds.call_args_list[1].kwargs == {"mhsparty": party_key}

    endpoint_audits = [call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "fhir_endpoint_trace"]
    assert len(endpoint_audits) == 1
    assert endpoint_audits[0]["outcome"] == AuditOutcome.ok

    identity_audits = [
        call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "resolve_asid_partykey"
    ]
    assert len(identity_audits) == 1
    assert identity_audits[0]["outcome"] == AuditOutcome.fail
    assert identity_audits[0]["error_code"] == "500"
    assert identity_audits[0]["detail"]["reason"] == "ambiguous_device_entries"

    mock_self_issuer.assert_not_awaited()
    client.assert_not_called()

    telemetry.assert_called_once()
    diagnostic = str(telemetry.call_args.args[0])
    assert "Ambiguous SDS Device response for ODS code E82665" in diagnostic

    for sensitive in (asid_1, asid_2, party_key, str(nhs_no)):
        assert sensitive not in diagnostic
        assert sensitive not in response.body.decode()
        assert sensitive not in caplog.text
        assert sensitive not in str(identity_audits[0]["detail"])


@pytest.mark.asyncio
async def test_zero_device_entries_fails_closed(mock_self_issuer):
    party_key = "FAKE-PARTY-KEY-99"
    nhs_no = 9690937278
    endpoint_trace = {
        "resourceType": "Bundle",
        "entry": [
            {
                "resource": {
                    "resourceType": "Endpoint",
                    "address": "https://gpconnect.example/fhir",
                    "identifier": [
                        {
                            "system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey",
                            "value": party_key,
                        }
                    ],
                }
            }
        ],
    }
    empty_device_bundle = {
        "resourceType": "Bundle",
        "entry": [],
    }

    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(nhs_no)),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.audit.audit.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.logging.record_application_failure"),
        patch("app.gpconnect_stages.transport.httpx.AsyncClient") as client,
    ):
        sds.side_effect = [endpoint_trace, empty_device_bundle]

        response = await gpconnect(nhs_no, saml_attrs=saml, request=get_mock_request())

    assert response.status_code == 500
    body = json.loads(response.body)
    assert body["success"] is False
    assert body["error"] == "Unable to find ASID for ODS code E82665"

    assert sds.call_count == 2
    assert sds.call_args_list[0].kwargs == {"endpoint": True}
    assert sds.call_args_list[1].kwargs == {"mhsparty": party_key}

    identity_audits = [
        call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "resolve_asid_partykey"
    ]
    assert len(identity_audits) == 1
    assert identity_audits[0]["outcome"] == AuditOutcome.fail
    assert identity_audits[0]["error_code"] == "500"

    mock_self_issuer.assert_not_awaited()
    client.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malformed_device",
    [
        None,
        {},
        {"resourceType": "Device"},
        {"resourceType": "Bundle", "entry": None},
    ],
)
async def test_non_bundle_device_response_fails_closed(mock_self_issuer, malformed_device):
    party_key = "FAKE-PARTY-KEY-99"
    nhs_no = 9690937278
    endpoint_trace = {
        "resourceType": "Bundle",
        "entry": [
            {
                "resource": {
                    "resourceType": "Endpoint",
                    "address": "https://gpconnect.example/fhir",
                    "identifier": [
                        {
                            "system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey",
                            "value": party_key,
                        }
                    ],
                }
            }
        ],
    }

    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(nhs_no)),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.audit.audit.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.logging.record_application_failure"),
        patch("app.gpconnect_stages.transport.httpx.AsyncClient") as client,
    ):
        sds.side_effect = [endpoint_trace, malformed_device]

        response = await gpconnect(nhs_no, saml_attrs=saml, request=get_mock_request())

    assert response.status_code == 500
    body = json.loads(response.body)
    assert body["success"] is False
    assert "Unable to parse SDS trace response" in body["error"]

    identity_audits = [
        call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "resolve_asid_partykey"
    ]
    assert len(identity_audits) == 1
    assert identity_audits[0]["outcome"] == AuditOutcome.fail
    assert identity_audits[0]["error_code"] == "500"

    mock_self_issuer.assert_not_awaited()
    client.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("audit_fails", [False, True])
async def test_endpoint_lookup_failure_is_audited_without_downstream_request(mock_self_issuer, audit_fails):

    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.audit.audit.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.gpconnect_stages.transport.httpx.AsyncClient") as client,
    ):
        sds.side_effect = [RuntimeError("SDS unavailable")]
        if audit_fails:

            async def fail_endpoint_audit(**kwargs):
                if kwargs["action"] == "fhir_endpoint_trace" and kwargs["outcome"] == AuditOutcome.fail:
                    raise AuditFailureException("Audit unavailable")

            audit.side_effect = fail_endpoint_audit
            with pytest.raises(AuditFailureException):
                await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())
        else:
            response = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())
            assert response.status_code == 502
            assert json.loads(response.body)["error"].startswith("SDS Endpoint resolution failed for ODS code E82665")
            assert "step:" in json.loads(response.body)["error"]
            assert "SDS unavailable" not in json.loads(response.body)["error"]

        endpoint_audits = [
            call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "fhir_endpoint_trace"
        ]
        assert len(endpoint_audits) == 1
        assert endpoint_audits[0]["outcome"] == AuditOutcome.fail
        assert endpoint_audits[0]["error_code"] == "502"
        assert endpoint_audits[0]["detail"] == {"step": "sds_endpoint.sds_lookup", "exception_type": "RuntimeError"}
        assert sds.call_count == 1
        assert not any(call.kwargs["action"] == "sds_trace" for call in audit.await_args_list)
        mock_self_issuer.assert_not_awaited()
        client.assert_not_called()


@pytest.mark.asyncio
@patch("app.audit.audit.attempt_audit", new_callable=AsyncMock)
@patch("app.logging.record_application_failure")
@patch("app.gpconnect_stages.transport.httpx.AsyncClient")
@patch("app.gpconnect_stages.transport.create_nhs_ssl_context")
@patch("app.gpconnect.sds_trace", new_callable=AsyncMock)
@patch("app.gpconnect.lookup_patient", new_callable=AsyncMock)
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
@pytest.mark.parametrize(
    "payload",
    ["not json", "[]", '{"resourceType":"OperationOutcome"}', '{"resourceType":"Bundle","entry":null}', "bob"],
)
@pytest.mark.parametrize("audit_fails", [False, True])
async def test_malformed_bundle_is_audited_and_recorded(payload, audit_fails):

    if payload == "bob":
        payload = Path("app/tests/fixtures/bundles/investigations/9465699896.json").read_text()
    with (
        patch("app.audit.audit.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.logging.record_application_failure") as telemetry,
        patch("app.gpconnect_stages.document.convert_bundle", new_callable=AsyncMock) as convert,
        patch("app.gpconnect_stages.document.redis_client.pipeline") as cache,
        patch("app.gpconnect_stages.transport.create_nhs_ssl_context"),
        patch("app.gpconnect_stages.transport.httpx.AsyncClient") as client,
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock) as pds,
    ):
        pds.return_value = load_pds(9690937278)
        sds.side_effect = [fake_sds_endpoint_trace(), fake_sds_device_trace()]
        client.return_value.__aenter__.return_value.post = AsyncMock(return_value=Response(200, content=payload))
        if audit_fails:

            async def fail_validation_audit(**kwargs):
                if kwargs.get("action") == "validate_fhir_bundle":
                    raise AuditFailureException("Audit unavailable")

            audit.side_effect = fail_validation_audit
            with pytest.raises(AuditFailureException):
                await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())
            assert telemetry.call_count == 2
            assert "exception_type: AuditFailureException" in str(telemetry.call_args.args[0])
            convert.assert_not_called()
            cache.assert_not_called()
            return
        response = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())
        assert response.status_code == 502
        body = json.loads(response.body)
        assert body["success"] is False
        assert body["error"].startswith("FHIR bundle malformed")
        assert "step:" in body["error"]
        telemetry.assert_called_once()
        event = audit.call_args.kwargs
        assert event["action"] == "validate_fhir_bundle"
        assert event["outcome"] == AuditOutcome.fail
        assert event["error_code"] == "502"
        assert event["nhs_number"] == "9690937278"
        expected = {
            "not json": ("json_decode", "JSONDecodeError"),
            "[]": ("resource_type", "ValueError"),
            '{"resourceType":"OperationOutcome"}': ("resource_type", "ValueError"),
            '{"resourceType":"Bundle","entry":null}': ("comment_entries", "TypeError"),
        }
        step, exception_type = expected.get(payload, ("fhir_models", "FHIRValidationError"))
        assert event["detail"] == {"step": "validate_fhir_bundle." + step, "exception_type": exception_type}
        convert.assert_not_called()
        cache.assert_not_called()


@pytest.mark.asyncio
@patch("app.audit.audit.attempt_audit", new_callable=AsyncMock)
@patch("app.gpconnect_stages.transport.httpx.AsyncClient")
@patch("app.gpconnect_stages.transport.create_nhs_ssl_context")
@patch("app.gpconnect.sds_trace", new_callable=AsyncMock)
@patch("app.gpconnect.lookup_patient", new_callable=AsyncMock)
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
@patch("app.gpconnect.sds_trace", new_callable=AsyncMock)
@patch("app.gpconnect.lookup_patient", new_callable=AsyncMock)
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


@pytest.mark.asyncio
@patch("app.audit.audit.attempt_audit", new_callable=AsyncMock)
@patch("app.logging.record_application_failure")
@patch("app.gpconnect_stages.transport.httpx.AsyncClient")
@patch("app.gpconnect_stages.transport.create_nhs_ssl_context")
@patch("app.gpconnect.sds_trace", new_callable=AsyncMock)
@patch("app.gpconnect.lookup_patient", new_callable=AsyncMock)
async def test_validate_fhir_bundle_data_minimisation(
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

    synthetic_body = "SYNTHETIC_FHIR_CONTENT_MUST_NOT_BE_AUDITED"
    mock_client = AsyncMock()
    mock_client.post.return_value = Response(status_code=200, content=synthetic_body)
    mock_async_client.return_value.__aenter__.return_value = mock_client

    result = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())

    assert result.status_code == 502
    body = json.loads(result.body)
    assert body["success"] is False
    assert body["error"].startswith("FHIR bundle malformed")
    assert "step:" in body["error"]

    audit_calls = mock_attempt_audit.call_args_list
    validate_fhir_call = [call for call in audit_calls if call.kwargs.get("action") == "validate_fhir_bundle"]
    assert validate_fhir_call

    audit_args = validate_fhir_call[-1].kwargs
    assert audit_args["outcome"] == AuditOutcome.fail
    assert audit_args["error_code"] == "502"

    assert "exception" not in audit_args["detail"]
    assert audit_args["detail"]["exception_type"] == "JSONDecodeError"

    for arg in audit_args.values():
        assert synthetic_body not in str(arg)


@pytest.mark.asyncio
@pytest.mark.parametrize("audit_fails", [False, True])
async def test_sds_device_failure_does_not_expose_upstream_secret(mock_self_issuer, audit_fails):

    upstream_secret = "SECRET_UPSTREAM_BODY"
    upstream_error = RuntimeError(f"Upstream HTTP 500: {upstream_secret}")

    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.audit.audit.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.logging.record_application_failure") as mock_record_failure,
        patch("app.gpconnect_stages.transport.httpx.AsyncClient") as client,
    ):
        sds.side_effect = [fake_sds_endpoint_trace(), upstream_error]

        if audit_fails:

            async def fail_device_audit(**kwargs):
                if kwargs["action"] == "sds_trace" and kwargs["outcome"] == AuditOutcome.fail:
                    raise AuditFailureException("Audit unavailable")

            audit.side_effect = fail_device_audit
            with pytest.raises(AuditFailureException):
                await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())

            assert mock_record_failure.call_count == 2
            assert "exception_type: AuditFailureException" in str(mock_record_failure.call_args.args[0])
        else:
            response = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())
            assert response.status_code == 502
            body = json.loads(response.body)
            assert body["success"] is False
            assert body["error"].startswith("SDS Trace failed for ODS code E82665")
            assert "step:" in body["error"]
            assert upstream_secret not in response.body.decode()

            mock_record_failure.assert_called_once()
            recorded_exception = mock_record_failure.call_args[0][0]
            assert isinstance(recorded_exception, RuntimeError)
            assert upstream_secret not in str(recorded_exception)
            assert upstream_secret not in repr(recorded_exception)
            assert str(recorded_exception) == (
                "SDS Trace failed for ODS code E82665 (step: sds_trace.sds_lookup, exception_type: RuntimeError)"
            )
            assert recorded_exception is not upstream_error

        # Audit verification
        endpoint_audits = [
            call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "fhir_endpoint_trace"
        ]
        assert len(endpoint_audits) == 1
        assert endpoint_audits[0]["outcome"] == AuditOutcome.ok

        device_audits = [call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "sds_trace"]
        assert len(device_audits) == 1
        assert device_audits[0]["outcome"] == AuditOutcome.fail
        assert device_audits[0]["error_code"] == "502"
        assert device_audits[0]["detail"] == {"step": "sds_trace.sds_lookup", "exception_type": "RuntimeError"}
        for arg in device_audits[0].values():
            assert upstream_secret not in str(arg)

        # Device failure prevents downstream GP Connect request
        assert sds.call_count == 2
        mock_self_issuer.assert_not_awaited()
        client.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("audit_fails", [False, True])
async def test_sds_endpoint_failure_does_not_expose_upstream_secret(mock_self_issuer, audit_fails):

    upstream_secret = "SECRET_UPSTREAM_BODY"
    upstream_error = RuntimeError(f"Upstream HTTP 500: {upstream_secret}")

    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.audit.audit.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.logging.record_application_failure") as mock_record_failure,
        patch("app.gpconnect_stages.transport.httpx.AsyncClient") as client,
    ):
        sds.side_effect = [upstream_error]

        if audit_fails:

            async def fail_endpoint_audit(**kwargs):
                if kwargs["action"] == "fhir_endpoint_trace" and kwargs["outcome"] == AuditOutcome.fail:
                    raise AuditFailureException("Audit unavailable")

            audit.side_effect = fail_endpoint_audit
            with pytest.raises(AuditFailureException):
                await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())

            assert mock_record_failure.call_count == 2
            assert "exception_type: AuditFailureException" in str(mock_record_failure.call_args.args[0])
        else:
            response = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())
            assert response.status_code == 502
            body = json.loads(response.body)
            assert body["success"] is False
            assert body["error"].startswith("SDS Endpoint resolution failed for ODS code E82665")
            assert "step:" in body["error"]
            assert upstream_secret not in response.body.decode()

            mock_record_failure.assert_called_once()
            recorded_exception = mock_record_failure.call_args[0][0]
            assert isinstance(recorded_exception, RuntimeError)
            assert upstream_secret not in str(recorded_exception)
            assert upstream_secret not in repr(recorded_exception)
            assert str(recorded_exception) == (
                "SDS Endpoint resolution failed for ODS code E82665 "
                "(step: sds_endpoint.sds_lookup, exception_type: RuntimeError)"
            )
            assert recorded_exception is not upstream_error

        # Audit verification
        endpoint_audits = [
            call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "fhir_endpoint_trace"
        ]
        assert len(endpoint_audits) == 1
        assert endpoint_audits[0]["outcome"] == AuditOutcome.fail
        assert endpoint_audits[0]["error_code"] == "502"
        assert endpoint_audits[0]["detail"] == {"step": "sds_endpoint.sds_lookup", "exception_type": "RuntimeError"}
        for arg in endpoint_audits[0].values():
            assert upstream_secret not in str(arg)

        # Endpoint failure stops before Device lookup
        assert sds.call_count == 1
        assert not any(call.kwargs["action"] == "sds_trace" for call in audit.await_args_list)
        mock_self_issuer.assert_not_awaited()
        client.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("audit_fails", [False, True])
async def test_resolve_gp_ods_failure_does_not_expose_upstream_secret(mock_self_issuer, audit_fails):

    upstream_secret = "SECRET_UPSTREAM_BODY"
    upstream_error = KeyError(f"Corrupt upstream PDS payload: {upstream_secret}")

    class SensitivePds(dict):
        def __getitem__(self, key):
            if key == "generalPractitioner":
                raise upstream_error
            return super().__getitem__(key)

    fake_pds = SensitivePds({"meta": {"security": [{"code": "U"}]}})

    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=fake_pds),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.audit.audit.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.logging.record_application_failure") as mock_record_failure,
        patch("app.gpconnect_stages.transport.httpx.AsyncClient") as client,
    ):
        if audit_fails:

            async def fail_resolve_audit(**kwargs):
                if kwargs["action"] == "resolve_gp_ods" and kwargs["outcome"] == AuditOutcome.fail:
                    raise AuditFailureException("Audit unavailable")

            audit.side_effect = fail_resolve_audit
            with pytest.raises(AuditFailureException):
                await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())

            assert mock_record_failure.call_count == 2
            assert "exception_type: AuditFailureException" in str(mock_record_failure.call_args.args[0])
        else:
            response = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())
            assert response.status_code == 502
            body = json.loads(response.body)
            assert body["success"] is False
            assert body["error"].startswith("Unable to read GP ODS from PDS response")
            assert "step:" in body["error"]
            assert upstream_secret not in response.body.decode()

            mock_record_failure.assert_called_once()
            recorded_exception = mock_record_failure.call_args[0][0]
            assert isinstance(recorded_exception, RuntimeError)
            assert upstream_secret not in str(recorded_exception)
            assert upstream_secret not in repr(recorded_exception)
            assert str(recorded_exception) == (
                "Unable to read GP ODS from PDS response (step: resolve_gp_ods.ods_extraction, exception_type: KeyError)"
            )
            assert recorded_exception is not upstream_error

        # Audit verification
        resolve_audits = [call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "resolve_gp_ods"]
        assert len(resolve_audits) == 1
        assert resolve_audits[0]["outcome"] == AuditOutcome.fail
        assert resolve_audits[0]["error_code"] == "502"
        assert resolve_audits[0]["detail"] == {"step": "resolve_gp_ods.ods_extraction", "exception_type": "KeyError"}
        for arg in resolve_audits[0].values():
            assert upstream_secret not in str(arg)

        # Downstream SDS never attempted
        sds.assert_not_awaited()
        mock_self_issuer.assert_not_awaited()
        client.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("audit_fails", [False, True])
async def test_resolve_asid_partykey_failure_does_not_expose_upstream_secret(mock_self_issuer, audit_fails):

    upstream_secret = "SECRET_UPSTREAM_BODY"
    safe_message = "Unable to find ASID for ODS code E82665"
    malformed_device = {
        "resourceType": "Bundle",
        "entry": [
            {
                "resource": {
                    "resourceType": "Device",
                    "identifier": [
                        {
                            "system": f"https://fhir.nhs.uk/Id/{upstream_secret}",
                            "value": upstream_secret,
                        }
                    ],
                }
            }
        ],
    }

    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.audit.audit.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.logging.record_application_failure") as mock_record_failure,
        patch("app.gpconnect_stages.transport.httpx.AsyncClient") as client,
    ):
        sds.side_effect = [fake_sds_endpoint_trace(), malformed_device]

        if audit_fails:

            async def fail_identity_audit(**kwargs):
                if kwargs["action"] == "resolve_asid_partykey" and kwargs["outcome"] == AuditOutcome.fail:
                    raise AuditFailureException("Audit unavailable")

            audit.side_effect = fail_identity_audit
            with pytest.raises(AuditFailureException):
                await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())

            assert mock_record_failure.call_count == 2
            assert "exception_type: AuditFailureException" in str(mock_record_failure.call_args.args[0])
        else:
            response = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())
            assert response.status_code == 500
            body = json.loads(response.body)
            assert body == {
                "success": False,
                "error": safe_message,
            }
            assert upstream_secret not in response.body.decode()

            mock_record_failure.assert_called_once()
            assert "step: resolve_asid_partykey.identity_validation" in str(mock_record_failure.call_args.args[0])

        # Stage 1 succeeded (Endpoint)
        endpoint_audits = [
            call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "fhir_endpoint_trace"
        ]
        assert len(endpoint_audits) == 1
        assert endpoint_audits[0]["outcome"] == AuditOutcome.ok

        # Stage 2 succeeded (Device trace)
        device_audits = [call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "sds_trace"]
        assert len(device_audits) == 1
        assert device_audits[0]["outcome"] == AuditOutcome.ok

        # Stage 3 failed (Device identity validation)
        identity_audits = [
            call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "resolve_asid_partykey"
        ]
        assert len(identity_audits) == 1
        assert identity_audits[0]["outcome"] == AuditOutcome.fail
        assert identity_audits[0]["error_code"] == "500"
        assert identity_audits[0]["detail"] == {
            "step": "resolve_asid_partykey.identity_validation",
            "message": safe_message,
        }
        for arg in identity_audits[0].values():
            assert upstream_secret not in str(arg)

        # GP Connect downstream request never attempted
        assert sds.call_count == 2
        mock_self_issuer.assert_not_awaited()
        client.assert_not_called()


@pytest.mark.asyncio
async def test_gpconnect_missing_party_key_fails_closed(mock_self_issuer):
    """Endpoint resource without nhsMhsPartyKey must fail closed with 502."""
    endpoint_without_party_key = {
        "resourceType": "Bundle",
        "entry": [
            {
                "resource": {
                    "resourceType": "Endpoint",
                    "address": "fake-endpoint-address",
                    "identifier": [{"system": "https://fhir.nhs.uk/Id/other", "value": "other"}],
                }
            }
        ],
    }
    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.audit.audit.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.logging.record_application_failure"),
        patch("app.gpconnect_stages.transport.httpx.AsyncClient") as client,
    ):
        sds.side_effect = [endpoint_without_party_key]
        response = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())

    assert response.status_code == 502
    body = json.loads(response.body)
    assert body["success"] is False
    assert "SDS Endpoint resolution failed for ODS code E82665" in body["error"]
    assert "step: sds_endpoint.partykey_validation" in body["error"]
    assert sds.call_count == 1
    endpoint_audits = [call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "fhir_endpoint_trace"]
    assert len(endpoint_audits) == 1
    assert endpoint_audits[0]["outcome"] == AuditOutcome.fail
    assert endpoint_audits[0]["error_code"] == "502"
    assert endpoint_audits[0]["detail"]["step"] == "sds_endpoint.partykey_validation"
    assert endpoint_audits[0]["detail"]["reason"] == "missing_nhs_mhs_party_key"
    assert not any(call.kwargs["action"] == "sds_trace" for call in audit.await_args_list)
    mock_self_issuer.assert_not_awaited()
    client.assert_not_called()


@pytest.mark.asyncio
async def test_gpconnect_ambiguous_party_key_fails_closed(mock_self_issuer):
    """Endpoint resource with multiple nhsMhsPartyKey values must fail closed with 502."""
    endpoint_with_ambiguous_party_key = {
        "resourceType": "Bundle",
        "entry": [
            {
                "resource": {
                    "resourceType": "Endpoint",
                    "address": "fake-endpoint-address",
                    "identifier": [
                        {"system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey", "value": "KEY-1"},
                        {"system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey", "value": "KEY-2"},
                    ],
                }
            }
        ],
    }
    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.audit.audit.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.logging.record_application_failure"),
        patch("app.gpconnect_stages.transport.httpx.AsyncClient") as client,
    ):
        sds.side_effect = [endpoint_with_ambiguous_party_key]
        response = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())

    assert response.status_code == 502
    body = json.loads(response.body)
    assert body["success"] is False
    assert "SDS Endpoint resolution failed for ODS code E82665" in body["error"]
    assert "step: sds_endpoint.partykey_validation" in body["error"]
    assert sds.call_count == 1
    endpoint_audits = [call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "fhir_endpoint_trace"]
    assert len(endpoint_audits) == 1
    assert endpoint_audits[0]["outcome"] == AuditOutcome.fail
    assert endpoint_audits[0]["error_code"] == "502"
    assert endpoint_audits[0]["detail"]["step"] == "sds_endpoint.partykey_validation"
    assert endpoint_audits[0]["detail"]["reason"] == "ambiguous_nhs_mhs_party_key"
    assert not any(call.kwargs["action"] == "sds_trace" for call in audit.await_args_list)
    mock_self_issuer.assert_not_awaited()
    client.assert_not_called()


@pytest.mark.asyncio
async def test_gpconnect_ambiguous_asid_fails_closed(mock_self_issuer):
    """Device resource with multiple nhsSpineASID values must fail closed with 500."""
    device_with_ambiguous_asid = {
        "resourceType": "Bundle",
        "entry": [
            {
                "resource": {
                    "resourceType": "Device",
                    "identifier": [
                        {"system": "https://fhir.nhs.uk/Id/nhsSpineASID", "value": "ASID-1"},
                        {"system": "https://fhir.nhs.uk/Id/nhsSpineASID", "value": "ASID-2"},
                    ],
                }
            }
        ],
    }
    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.audit.audit.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.logging.record_application_failure"),
        patch("app.gpconnect_stages.transport.httpx.AsyncClient") as client,
    ):
        sds.side_effect = [fake_sds_endpoint_trace(), device_with_ambiguous_asid]
        response = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())

    assert response.status_code == 500
    body = json.loads(response.body)
    assert body["success"] is False
    assert body["error"] == "Ambiguous ASID for ODS code E82665"
    assert sds.call_count == 2
    identity_audits = [
        call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "resolve_asid_partykey"
    ]
    assert len(identity_audits) == 1
    assert identity_audits[0]["outcome"] == AuditOutcome.fail
    assert identity_audits[0]["error_code"] == "500"
    assert identity_audits[0]["detail"]["step"] == "resolve_asid_partykey.identity_validation"
    mock_self_issuer.assert_not_awaited()
    client.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure_stage,sds_mock",
    [
        ("resolve_gp_ods", None),
        ("sds_endpoint", [RuntimeError("upstream endpoint failure")]),
        ("sds_trace", [fake_sds_endpoint_trace(), RuntimeError("upstream device failure")]),
        (
            "resolve_asid_partykey",
            [
                fake_sds_endpoint_trace(),
                {
                    "resourceType": "Bundle",
                    "entry": [
                        {
                            "resource": {
                                "resourceType": "Device",
                                "identifier": [{"system": "https://fhir.nhs.uk/Id/other", "value": "other"}],
                            }
                        }
                    ],
                },
            ],
        ),
    ],
)
async def test_raw_nhs_number_absent_from_application_logging(mock_self_issuer, failure_stage, sds_mock, caplog):
    """Raw NHS numbers must never appear in application logs during failure paths."""

    pds = load_pds(9690937278)
    if failure_stage == "resolve_gp_ods":
        pds = {"meta": {"security": [{"code": "U"}]}, "generalPractitioner": []}

    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=pds),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.audit.audit.attempt_audit", new_callable=AsyncMock),
        patch("app.logging.record_application_failure"),
        patch("app.gpconnect_stages.transport.httpx.AsyncClient"),
        caplog.at_level(logging.DEBUG),
    ):
        if sds_mock:
            sds.side_effect = sds_mock

        response = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())
        assert response.status_code in (500, 502)

        # Assert raw NHS number is strictly absent from all logged records
        assert "9690937278" not in caplog.text
        assert "NHS=" not in caplog.text


@pytest.fixture
def observed_pipeline(monkeypatch, tmp_path):
    """Observe real orchestration, validation, conversion and audit building with synthetic I/O.

    Only dependency import targets should need updating after extraction. The PDS
    patient cache is warm; the document cache is cold. No clinical fixtures,
    network, database or Redis connections are used by this fixture.
    """
    gp = importlib.import_module("app.gpconnect")
    pds = importlib.import_module("app.pds.pds")
    iti38 = importlib.import_module("app.soap.responses.iti_38")
    audit_build = importlib.import_module("app.audit.build")
    audit_store = importlib.import_module("app.audit.store")
    telemetry = importlib.import_module("app.telemetry")
    probe = SimpleNamespace(
        gp=gp,
        document=importlib.import_module("app.gpconnect_stages.document"),
        transport=importlib.import_module("app.gpconnect_stages.transport"),
        nhs="9999999999",  # Fabricated checksum-valid identifier, never sent externally.
        events=[],
        event_spans=[],
        failures={},
        audits=[],
        persisted=[],
        fail_audit=None,
        commands=[],
        published=False,
        ttl=1.5,
        endpoint=fake_sds_endpoint_trace(),
        device=fake_sds_device_trace(),
        saml=SAMLAttributes(
            subject_id="Synthetic, Clinician",
            organization="Synthetic Organisation",
            organization_id="urn:oid:1.2.826.0.1.3680043.10.999",
            role=CD(code="synthetic-role", codeSystemName="synthetic-system"),
        ),
    )

    def note(event):
        probe.events.append(event)
        probe.event_spans.append((event, trace.get_current_span().get_span_context().span_id))
        if event in probe.failures:
            raise probe.failures[event]

    probe.note = note
    patient = {
        "resourceType": "Patient",
        "id": "synthetic-patient",
        "identifier": [{"system": "https://fhir.nhs.uk/Id/nhs-number", "value": probe.nhs}],
        "name": [{"family": "Synthetic", "given": ["Patient"]}],
        "birthDate": "2000-01-01",
    }
    probe.patient = {
        **patient,
        "meta": {"security": [{"code": "U"}]},
        "generalPractitioner": [{"identifier": {"value": "SYN001"}}],
    }
    probe.http_response = Response(
        200, json={"resourceType": "Bundle", "type": "collection", "entry": [{"resource": patient}]}
    )
    probe.exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(probe.exporter))
    probe.tracer = provider.get_tracer("synthetic-pipeline-test")
    monkeypatch.setattr(telemetry, "tracer", probe.tracer)
    monkeypatch.setattr(telemetry, "stage_duration", MagicMock())
    monkeypatch.setattr(telemetry, "cache_lookups", MagicMock())
    monkeypatch.setenv("ENV", "int")  # Suppress the optional local XML write unless explicitly tested.
    monkeypatch.setenv("ORG_ASID", "synthetic-consumer-asid")
    monkeypatch.setenv("CCDA_EXPIRY_HOURS", "9")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gp, "USE_RELAY", False)
    monkeypatch.setattr(gp, "environment", "int")
    monkeypatch.setattr(gp, "OVER_INTERNET_PATH", "https://proxy.example.invalid")
    monkeypatch.setattr(
        probe.transport, "get_nhs_cert_paths", lambda: ("synthetic-cert", "synthetic-key", "synthetic-ca")
    )
    monkeypatch.setattr(probe.transport, "create_nhs_ssl_context", MagicMock())

    # Exercise the real attempt_audit/build_audit_event, replacing only persistence.
    session = SimpleNamespace(commit=AsyncMock())
    session_context = AsyncMock()
    session_context.__aenter__.return_value = session
    session_factory = MagicMock(return_value=session_context)
    sequence = iter(range(1, 100))
    monkeypatch.setattr(audit_build, "next_audit_sequence", AsyncMock(side_effect=lambda _: next(sequence)))

    async def insert(_session, event):
        if event.event.action == probe.fail_audit:
            raise RuntimeError("synthetic persistence failure")
        probe.persisted.append(event)

    monkeypatch.setattr(audit_store, "insert_audit_event", insert)
    original_audit = gp.attempt_audit

    async def audit(**kwargs):
        note(f"audit:{kwargs['action']}:{kwargs['outcome'].value}")
        probe.audits.append(kwargs)
        await original_audit(**kwargs)

    for module in (importlib.import_module("app.audit.audit"), pds, iti38):
        monkeypatch.setattr(module, "attempt_audit", audit)

    class ExpiryState(SimpleNamespace):
        @property
        def ccda_expiry_hours(self):
            # hasattr and the value read both access this property. Observe the
            # configuration boundary, without constraining that access count.
            if "ttl" not in probe.events:
                note("ttl")
            return probe.ttl

    probe.state = ExpiryState(SessionLocal=session_factory)
    probe.request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/SOAP/iti38",
            "headers": [(b"x-request-id", b"synthetic-inbound-request")],
            "client": ("127.0.0.1", 12345),
            "app": SimpleNamespace(state=probe.state),
        }
    )
    original_validate = gp.validateNHSnumber

    def validate(value):
        note("nhs.validate")
        return original_validate(value)

    monkeypatch.setattr(gp, "validateNHSnumber", validate)
    original_patient = gp.lookup_patient

    async def lookup(*args, **kwargs):
        note("pds")
        return await original_patient(*args, **kwargs)

    async def patient_cache(_key):
        return json.dumps(probe.patient).encode()

    monkeypatch.setattr(gp, "lookup_patient", lookup)
    monkeypatch.setattr(pds, "redis_client", SimpleNamespace(get=AsyncMock(side_effect=patient_cache)))

    async def sds(ods, **kwargs):
        assert ods == "SYN001"
        if kwargs == {"endpoint": True}:
            note("sds.endpoint")
            return probe.endpoint
        assert kwargs == {"mhsparty": "FAKE-PARTY-KEY"}
        note("sds.device")
        return probe.device

    async def issuer():
        note("issuer")
        return "https://consumer.example.invalid/spine"

    monkeypatch.setattr(gp, "sds_trace", sds)
    monkeypatch.setattr(gp, "lookup_self_issuer", issuer)
    original_jwt = gp.create_jwt

    def token(*args, **kwargs):
        note("jwt")
        return original_jwt(*args, **kwargs)

    monkeypatch.setattr(gp, "create_jwt", token)
    original_inject = gp.inject

    def inject(headers):
        note("inject")
        original_inject(headers)

    monkeypatch.setattr(gp, "inject", inject)

    async def post(url, *, json, headers):
        note("http.post")
        probe.sent = SimpleNamespace(url=url, body=json, headers=dict(headers))
        note("http.response")
        return probe.http_response

    client = AsyncMock()
    client.post.side_effect = post
    monkeypatch.setattr(probe.transport.httpx, "AsyncClient", MagicMock(return_value=client))
    client.__aenter__.return_value = client
    original_bundle = probe.document.bundle.Bundle

    def model(payload):
        note("fhir.models")
        return original_bundle(payload)

    monkeypatch.setattr(probe.document, "bundle", SimpleNamespace(Bundle=model))
    original_convert = probe.document.convert_bundle

    async def convert(bundle, index):
        note("convert")
        return await original_convert(bundle, index)

    monkeypatch.setattr(probe.document, "convert_bundle", convert)
    original_serialize = probe.document.base64_xml

    def serialize(document):
        note("serialize")
        return original_serialize(document)

    monkeypatch.setattr(probe.document, "base64_xml", serialize)

    class Pipeline:
        async def __aenter__(self):
            note("redis.enter")
            return self

        async def __aexit__(self, *_):
            note("redis.exit")

        def setex(self, key, ttl, value):
            note("redis.setex")
            probe.commands.append(("setex", key, ttl, value))

        def hset(self, key, *, mapping):
            note("redis.hset")
            probe.commands.append(("hset", key, mapping))

        def expire(self, key, ttl):
            note("redis.expire")
            probe.commands.append(("expire", key, ttl))

        async def execute(self):
            note("redis.execute")
            probe.published = True

    monkeypatch.setattr(probe.document, "redis_client", SimpleNamespace(pipeline=Pipeline))
    monkeypatch.setattr(iti38, "redis_client", SimpleNamespace(get=AsyncMock(return_value=None)))
    original_response = gp.JSONResponse

    def response(*args, **kwargs):
        note("json.response")
        return original_response(*args, **kwargs)

    monkeypatch.setattr(gp, "JSONResponse", response)
    monkeypatch.setattr(probe.document, "JSONResponse", response)
    try:
        yield probe
    finally:
        provider.shutdown()


@pytest.mark.asyncio
async def test_pipeline_characterization_success_order_and_publication(observed_pipeline):
    """A cold document path audits, validates and publishes in this observable order."""
    p = observed_pipeline
    response = await p.gp.gpconnect(p.nhs, p.saml, request=p.request)
    assert response.status_code == 200
    assert p.events == [
        "nhs.validate",
        "pds",
        "audit:pds_lookup:ok",
        "sds.endpoint",
        "audit:fhir_endpoint_trace:ok",
        "sds.device",
        "audit:sds_trace:ok",
        "issuer",
        "jwt",
        "audit:sds_self_lookup:ok",
        "inject",
        "http.post",
        "http.response",
        "audit:gpconnect_request:ok",
        "fhir.models",
        "convert",
        "serialize",
        "audit:store_ccda:ok",
        "ttl",
        "redis.enter",
        "redis.setex",
        "redis.hset",
        "redis.expire",
        "redis.execute",
        "redis.exit",
        "json.response",
    ]
    body = json.loads(response.body)
    document_id = body["document_id"]
    assert body == {"success": True, "document_id": document_id}
    assert [command[0] for command in p.commands] == ["setex", "hset", "expire"]
    assert p.commands[0] == ("setex", p.nhs, timedelta(hours=1.5), document_id)
    assert p.commands[1][1] == f"document:{document_id}"
    assert p.commands[1][2]["nhs_number"] == p.nhs
    assert b"ClinicalDocument" in base64.b64decode(p.commands[1][2]["content"])
    assert p.commands[2] == ("expire", f"document:{document_id}", timedelta(hours=1.5))
    assert p.persisted[-1].event.data_refs.document_id == document_id
    assert p.sent.headers["Ssp-To"] == "200000000000"
    assert p.sent.headers["Ssp-From"] == "synthetic-consumer-asid"
    assert p.sent.body["parameter"][0]["valueIdentifier"]["value"] == p.nhs
    assert p.published


@pytest.mark.asyncio
@pytest.mark.parametrize("restricted", [True, False], ids=["restricted", "missing-security"])
async def test_pipeline_characterization_restriction_precedes_routing(observed_pipeline, restricted):
    """Observe the restriction boundary through denial, without spying on dictionary indexing."""
    p = observed_pipeline
    p.patient["meta"] = {"security": [{"code": "R"}]} if restricted else {}
    response = await p.gp.gpconnect(p.nhs, p.saml, request=p.request)
    assert response.status_code == 403
    assert p.events == ["nhs.validate", "pds", "audit:pds_lookup:ok", "audit:check_restriction:deny", "json.response"]
    assert p.audits[-1]["detail"] == {"step": "check_restriction.restriction_check"}
    assert p.commands == []


@pytest.mark.asyncio
async def test_pipeline_characterization_store_audit_failure_stops_before_ttl(observed_pipeline):
    p = observed_pipeline
    p.fail_audit = "store_ccda"
    with pytest.raises(AuditFailureException, match="^GP Connect audit persistence failed$"):
        await p.gp.gpconnect(p.nhs, p.saml, request=p.request)
    assert p.events[-2:] == ["serialize", "audit:store_ccda:ok"]
    assert "ttl" not in p.events
    assert p.commands == []
    assert not p.published
    assert [event.event.action for event in p.persisted][-1] == "gpconnect_request"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "boundary,step,status,exception_type,prefix,last_audit",
    [
        ("pds", "pds_lookup.lookup_patient", 502, "RuntimeError", "PDS lookup failed", None),
        (
            "sds.endpoint",
            "sds_endpoint.sds_lookup",
            502,
            "RuntimeError",
            "SDS Endpoint resolution failed for ODS code SYN001",
            ("fhir_endpoint_trace", "fail"),
        ),
        (
            "sds.device",
            "sds_trace.sds_lookup",
            502,
            "RuntimeError",
            "SDS Trace failed for ODS code SYN001",
            ("sds_trace", "fail"),
        ),
        (
            "asid",
            "resolve_asid_partykey.device_extraction",
            500,
            "AttributeError",
            "Unable to parse SDS trace response",
            ("resolve_asid_partykey", "fail"),
        ),
        (
            "issuer",
            "sds_self_lookup.issuer_lookup",
            502,
            "RuntimeError",
            "SDS consumer issuer lookup failed",
            ("sds_self_lookup", "fail"),
        ),
        (
            "http.post",
            "gpconnect_request.http_post",
            502,
            "RuntimeError",
            "Transport error",
            ("gpconnect_request", "fail"),
        ),
        (
            "decode",
            "validate_fhir_bundle.json_decode",
            502,
            "JSONDecodeError",
            "FHIR bundle malformed",
            ("validate_fhir_bundle", "fail"),
        ),
        (
            "models",
            "validate_fhir_bundle.fhir_models",
            502,
            "FHIRValidationError",
            "FHIR bundle malformed",
            ("validate_fhir_bundle", "fail"),
        ),
        (
            "convert",
            "ccda_convert.convert_bundle",
            500,
            "RuntimeError",
            "Failed to convert FHIR Bundle to CCDA",
            ("gpconnect_request", "ok"),
        ),
        (
            "serialize",
            "ccda_serialize.base64_xml",
            500,
            "RuntimeError",
            "GP Connect failed",
            ("gpconnect_request", "ok"),
        ),
        ("ttl", "ccda_cache.expiry_configuration", 500, "RuntimeError", "GP Connect failed", ("store_ccda", "ok")),
        (
            "redis.execute",
            "ccda_cache.pipeline_execute",
            500,
            "RuntimeError",
            "GP Connect failed",
            ("store_ccda", "ok"),
        ),
        ("redis.exit", "ccda_cache.pipeline_close", 500, "RuntimeError", "GP Connect failed", ("store_ccda", "ok")),
    ],
)
async def test_pipeline_characterization_failure_steps(
    observed_pipeline, caplog, boundary, step, status, exception_type, prefix, last_audit
):
    """Faults at real boundaries retain status, exact step and the existing audit outcome."""
    p = observed_pipeline
    if boundary == "asid":
        p.device = {"resourceType": "Bundle", "entry": [{"resource": None}]}
    elif boundary == "decode":
        p.http_response = Response(200, text="not synthetic JSON")
    elif boundary == "models":
        p.http_response = Response(200, json={"resourceType": "Bundle", "entry": []})  # Required type absent.
    else:
        p.failures[boundary] = RuntimeError("synthetic private failure")
    response = await p.gp.gpconnect(p.nhs, p.saml, request=p.request)
    diagnostic = f"{prefix} (step: {step}, exception_type: {exception_type})"
    assert response.status_code == status
    assert json.loads(response.body) == {"success": False, "error": diagnostic}
    assert diagnostic in caplog.text
    assert "synthetic private failure" not in caplog.text + response.body.decode()
    actual_audits = [(event["action"], event["outcome"].value) for event in p.audits]
    if last_audit is None:
        assert actual_audits == []
    else:
        assert actual_audits[-1] == last_audit
        if last_audit[1] == "fail":
            assert p.audits[-1]["detail"]["step"] == step
    if boundary in {"ttl", "redis.execute", "redis.exit"}:
        assert actual_audits[-2:] == [("gpconnect_request", "ok"), ("store_ccda", "ok")]
    else:
        assert not any(action == "store_ccda" for action, _ in actual_audits)
        assert p.commands == []
    if boundary in {"decode", "models"}:
        assert actual_audits[-2:] == [("gpconnect_request", "ok"), ("validate_fhir_bundle", "fail")]
        assert "convert" not in p.events
    if boundary == "asid":
        assert actual_audits[-2:] == [("sds_trace", "ok"), ("resolve_asid_partykey", "fail")]
    # Exit failure happens after a successful execute; there is no compensating audit.
    assert p.published is (boundary == "redis.exit")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case,status,expected_audits,step",
    [
        ("invalid-nhs", 400, [("validate_nhs_number", "fail")], "validate_nhs_number.validate"),
        ("pds-not-found", 404, [], None),
        ("missing-ods", 502, [("pds_lookup", "ok"), ("resolve_gp_ods", "fail")], "resolve_gp_ods.ods_extraction"),
        ("no-endpoint", 502, [("pds_lookup", "ok"), ("fhir_endpoint_trace", "fail")], "sds_endpoint.entry_validation"),
        (
            "no-device",
            500,
            [
                ("pds_lookup", "ok"),
                ("fhir_endpoint_trace", "ok"),
                ("sds_trace", "ok"),
                ("resolve_asid_partykey", "fail"),
            ],
            "resolve_asid_partykey.identity_validation",
        ),
    ],
)
async def test_pipeline_characterization_early_return_audits(observed_pipeline, case, status, expected_audits, step):
    p = observed_pipeline
    nhs = p.nhs
    if case == "invalid-nhs":
        nhs = "123"
    elif case == "pds-not-found":
        # The GP boundary does not manufacture another PDS audit for a helper exception.
        p.failures["pds"] = HTTPException(404, "Patient not found on PDS")
    elif case == "missing-ods":
        p.patient["generalPractitioner"] = []
    elif case == "no-endpoint":
        p.endpoint = {"resourceType": "Bundle", "entry": []}
    else:
        p.device = {"resourceType": "Bundle", "entry": []}
    response = await p.gp.gpconnect(nhs, p.saml, request=p.request)
    assert response.status_code == status
    assert [(a["action"], a["outcome"].value) for a in p.audits] == expected_audits
    if step:
        assert p.audits[-1]["detail"]["step"] == step
    if case == "pds-not-found":
        assert json.loads(response.body) == {
            "success": False,
            "error": "Patient not found on PDS",
            "xds_error_code": "XDSUnknownPatientId",
        }
    if case == "no-endpoint":
        assert p.audits[-1]["detail"]["reason"] == "no_gp_connect_structured_record_route"
    assert "issuer" not in p.events
    assert "http.post" not in p.events
    assert p.commands == []


@pytest.mark.asyncio
@pytest.mark.parametrize("state_present", [True, False], ids=["app-state-precedes-env", "environment-fallback"])
async def test_pipeline_characterization_ttl_source(observed_pipeline, state_present):
    p = observed_pipeline
    if not state_present:
        p.request.app.state = SimpleNamespace(SessionLocal=p.state.SessionLocal)
    response = await p.gp.gpconnect(p.nhs, p.saml, request=p.request)
    assert response.status_code == 200
    ttl = timedelta(hours=1.5 if state_present else 9)
    assert p.commands[0][2] == p.commands[2][2] == ttl
    assert p.events.index("audit:store_ccda:ok") < p.events.index("redis.enter")


@pytest.mark.asyncio
@pytest.mark.parametrize("write_fails", [False, True], ids=["written-after-publication", "failure-after-publication"])
async def test_pipeline_characterization_local_xml_after_publication(observed_pipeline, monkeypatch, write_fails):
    p = observed_pipeline
    monkeypatch.setenv("ENV", "dev")
    original_open = open

    def local_open(path, *args, **kwargs):
        p.note("local.xml")
        assert p.published
        assert p.events.index("redis.exit") < p.events.index("local.xml")
        if write_fails:
            raise OSError("synthetic private filesystem error")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(p.document, "open", local_open, raising=False)
    response = await p.gp.gpconnect(p.nhs, p.saml, request=p.request)
    assert p.events[-2:] == ["local.xml", "json.response"]
    assert p.persisted[-1].event.action == "store_ccda"
    assert p.persisted[-1].event.outcome == AuditOutcome.ok
    assert p.published
    if write_fails:
        assert response.status_code == 500
        assert json.loads(response.body) == {
            "success": False,
            "error": "GP Connect failed (step: ccda_serialize.local_xml_file, exception_type: OSError)",
        }
    else:
        assert response.status_code == 200
        assert "ClinicalDocument" in Path(f"{p.nhs}.xml").read_text()


@pytest.mark.asyncio
async def test_pipeline_characterization_custom_span_placement(observed_pipeline):
    """Assert application spans only, using an isolated provider and no auto-instrumentation."""
    p = observed_pipeline
    with p.tracer.start_as_current_span("synthetic.request") as parent:
        response = await p.gp.gpconnect(p.nhs, p.saml, request=p.request)
        parent_context = parent.get_span_context()
    assert response.status_code == 200
    spans = p.exporter.get_finished_spans()
    by_name = {span.name: span for span in spans}
    assert sorted(span.name for span in spans) == sorted(
        [
            "synthetic.request",
            "xhuma.pds.lookup",
            "xhuma.pds.cache.read",
            "xhuma.sds.endpoint",
            "xhuma.sds.trace",
            "xhuma.sds.self",
            "xhuma.gpconnect.http",
            "xhuma.fhir.decode",
            "xhuma.fhir.models",
            "xhuma.fhir.index",
            "FHIR2CCDA.convert_bundle",
            "xhuma.ccda.serialize",
            "xhuma.ccda.cache.write",
            *(["xhuma.audit.persist"] * 6),
        ]
    )
    assert by_name["xhuma.gpconnect.http"].attributes["transport"] == "direct"
    assert by_name["xhuma.fhir.models"].attributes["resource_count"] == 1
    assert by_name["FHIR2CCDA.convert_bundle"].attributes["resource_count"] == 1
    assert by_name["xhuma.ccda.serialize"].attributes["document.base64_bytes"] == len(p.commands[1][2]["content"])
    assert by_name["xhuma.ccda.cache.write"].attributes["document_bytes"] == len(p.commands[1][2]["content"])
    for span in spans:
        if span.name != "synthetic.request":
            assert span.attributes["stage.outcome"] == "ok"
            assert span.attributes["stage.duration_ms"] >= 0
    active = dict(p.event_spans)
    assert active["inject"] == parent_context.span_id
    assert active["http.post"] == by_name["xhuma.gpconnect.http"].context.span_id
    assert active["audit:gpconnect_request:ok"] == parent_context.span_id
    assert active["convert"] == by_name["FHIR2CCDA.convert_bundle"].context.span_id
    assert by_name["xhuma.gpconnect.http"].parent.span_id == parent_context.span_id
    propagated = trace.get_current_span(extract(p.sent.headers)).get_span_context()
    assert (propagated.trace_id, propagated.span_id) == (parent_context.trace_id, parent_context.span_id)
    assert p.events.index("inject") < p.events.index("http.post")
    assert (
        p.events.index("http.response") < p.events.index("audit:gpconnect_request:ok") < p.events.index("fhir.models")
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("http_status", [200, 503], ids=["200-invalid-fhir", "503-outside-http-span"])
async def test_pipeline_characterization_http_success_is_not_document_success(observed_pipeline, http_status):
    p = observed_pipeline
    p.http_response = Response(http_status, text="not synthetic JSON")
    with p.tracer.start_as_current_span("synthetic.request") as parent:
        response = await p.gp.gpconnect(p.nhs, p.saml, request=p.request)
        parent_id = parent.get_span_context().span_id
    spans = {span.name: span for span in p.exporter.get_finished_spans()}
    transport = spans["xhuma.gpconnect.http"]
    assert transport.attributes["stage.outcome"] == "ok"
    assert transport.status.status_code == StatusCode.UNSET
    assert spans["synthetic.request"].status.status_code == StatusCode.ERROR
    assert p.commands == []
    assert "convert" not in p.events
    active = dict(p.event_spans)
    if http_status == 200:
        assert response.status_code == 502
        assert [(a["action"], a["outcome"].value) for a in p.audits][-2:] == [
            ("gpconnect_request", "ok"),
            ("validate_fhir_bundle", "fail"),
        ]
        assert active["audit:gpconnect_request:ok"] == parent_id
        assert spans["xhuma.fhir.decode"].attributes["stage.outcome"] == "error"
    else:
        assert response.status_code == 503
        assert json.loads(response.body)["error"] == "Error from GP Connect endpoint 503"
        assert active["audit:gpconnect_request:fail"] == parent_id
        assert p.audits[-1]["detail"]["step"] == "gpconnect_request.response_status"
        assert "xhuma.fhir.decode" not in spans
    assert not any(a["action"] == "store_ccda" for a in p.audits)


@pytest.mark.asyncio
async def test_pipeline_characterization_iti38_cache_miss_journey(observed_pipeline, monkeypatch):
    """Enter the SOAP route; use real SAML, GP orchestration, CDA and audit event builders."""
    from app.soap import soap

    iti38 = importlib.import_module("app.soap.responses.iti_38")
    p = observed_pipeline
    monkeypatch.setenv("SAML_TRUSTED_ISSUER", "urn:synthetic:trusted-issuer")
    app = FastAPI()
    app.state = p.state
    app.include_router(soap.router)
    soap.register_handlers(app)
    message_id = "urn:uuid:11111111-1111-4111-8111-111111111111"
    query_id = "urn:uuid:22222222-2222-4222-8222-222222222222"
    payload = f"""
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
 xmlns:wsa="http://www.w3.org/2005/08/addressing"
 xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
 xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion"
 xmlns:q="urn:oasis:names:tc:ebxml-regrep:xsd:query:3.0"
 xmlns:rim="urn:oasis:names:tc:ebxml-regrep:xsd:rim:3.0">
 <s:Header>
  <wsa:MessageID>{message_id}</wsa:MessageID>
  <wsse:Security><saml:Assertion>
   <saml:Issuer>urn:synthetic:trusted-issuer</saml:Issuer>
   <saml:AttributeStatement>
    <saml:Attribute Name="urn:oasis:names:tc:xspa:1.0:subject:subject-id">
     <saml:AttributeValue>Synthetic, Clinician</saml:AttributeValue>
    </saml:Attribute>
    <saml:Attribute Name="urn:oasis:names:tc:xspa:1.0:subject:organization">
     <saml:AttributeValue>Synthetic Organisation</saml:AttributeValue>
    </saml:Attribute>
    <saml:Attribute Name="urn:oasis:names:tc:xspa:1.0:subject:organization-id">
     <saml:AttributeValue>urn:oid:1.2.826.0.1.3680043.10.999</saml:AttributeValue>
    </saml:Attribute>
    <saml:Attribute Name="urn:oasis:names:tc:xacml:2.0:subject:role">
     <saml:AttributeValue><Role code="synthetic-role" /></saml:AttributeValue>
    </saml:Attribute>
   </saml:AttributeStatement>
  </saml:Assertion></wsse:Security>
 </s:Header>
 <s:Body><q:AdhocQueryRequest><rim:AdhocQuery id="{query_id}">
  <rim:Slot name="$XDSDocumentEntryPatientId"><rim:ValueList>
   <rim:Value>{p.nhs}^^^&amp;2.16.840.1.113883.2.1.4.1&amp;ISO</rim:Value>
  </rim:ValueList></rim:Slot>
 </rim:AdhocQuery></q:AdhocQueryRequest></s:Body>
</s:Envelope>
"""
    with p.tracer.start_as_current_span("synthetic.request") as parent:
        with TestClient(app) as client:
            response = client.post(
                "/SOAP/iti38",
                content=payload,
                headers={"Content-Type": "application/soap+xml", "X-Request-ID": "synthetic-inbound-request"},
            )
        trace_id = f"{parent.get_span_context().trace_id:032x}"
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/soap+xml")
    root = ET.fromstring(response.content)
    assert root.findtext(".//{http://www.w3.org/2005/08/addressing}RelatesTo") == message_id
    query = root.find(".//{urn:oasis:names:tc:ebxml-regrep:xsd:query:3.0}AdhocQueryResponse")
    assert query.attrib["status"] == "urn:oasis:names:tc:ebxml-regrep:ResponseStatusType:Success"
    assert root.find(".//{urn:oasis:names:tc:ebxml-regrep:xsd:rs:3.0}RegistryError") is None
    document = root.find(".//{urn:oasis:names:tc:ebxml-regrep:xsd:rim:3.0}ExtrinsicObject")
    document_id = document.attrib["id"]
    assert p.commands[0][3] == document_id
    assert p.published
    iti38.redis_client.get.assert_awaited_once_with(p.nhs)
    assert [event.event.action for event in p.persisted] == [
        "pds_lookup",
        "fhir_endpoint_trace",
        "sds_trace",
        "sds_self_lookup",
        "gpconnect_request",
        "store_ccda",
        "iti38_document_query",
    ]
    assert all(event.event.outcome == AuditOutcome.ok for event in p.persisted)
    assert all(event.trace_id == trace_id for event in p.persisted)
    assert all(event.saml.subject_id == "Synthetic, Clinician" for event in p.persisted)
    assert len({event.subject_ref for event in p.persisted}) == 1
    for event in p.persisted:
        expected_id = (
            p.sent.headers["Ssp-TraceID"] if event.event.action == "gpconnect_request" else "synthetic-inbound-request"
        )
        assert event.request_id == expected_id
    assert p.sent.headers["Ssp-TraceID"] != "synthetic-inbound-request"
    assert p.persisted[-1].event.data_refs.message_id == message_id
    assert p.persisted[-1].event.data_refs.document_id == document_id
    assert p.persisted[-1].event.detail == {"cache_hit": False}
    assert p.events.index("redis.exit") < p.events.index("audit:iti38_document_query:ok")
    decoded = ET.fromstring(base64.b64decode(p.commands[1][2]["content"]))
    patient_id = decoded.find(".//{urn:hl7-org:v3}recordTarget/{urn:hl7-org:v3}patientRole/{urn:hl7-org:v3}id")
    assert patient_id.attrib["extension"] == p.nhs


@pytest.mark.asyncio
async def test_transport_direct_configuration(observed_pipeline):
    p = observed_pipeline
    response = await p.gp.gpconnect(p.nhs, p.saml, request=p.request)
    assert response.status_code == 200
    assert p.sent.url == "https://proxy.example.invalid/fake-gpconnect-endpoint/Patient/$gpc.getstructuredrecord"
    p.transport.create_nhs_ssl_context.assert_called_once_with("synthetic-cert", "synthetic-key", "synthetic-ca")
    kwargs = p.transport.httpx.AsyncClient.call_args.kwargs
    assert kwargs == {
        "cert": ("synthetic-cert", "synthetic-key"),
        "verify": p.transport.create_nhs_ssl_context.return_value,
        "timeout": httpx.Timeout(30.0),
        "http2": False,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "boundary,step,error",
    [
        ("entry", "http_client", RuntimeError("synthetic entry")),
        ("exit", "http_client_close", RuntimeError("synthetic exit")),
        ("post", "http_post", httpx.ReadError("synthetic read")),
        ("paths", "certificate_paths", FileNotFoundError("synthetic paths")),
        ("tls", "tls_context", FileNotFoundError("synthetic tls")),
    ],
)
async def test_transport_direct_failure_steps(observed_pipeline, monkeypatch, boundary, step, error):
    p = observed_pipeline
    client = p.transport.httpx.AsyncClient.return_value
    if boundary == "entry":
        client.__aenter__.side_effect = error
    elif boundary == "exit":
        client.__aexit__.side_effect = error
    elif boundary == "post":
        client.post.side_effect = error
    else:
        name = "get_nhs_cert_paths" if boundary == "paths" else "create_nhs_ssl_context"
        monkeypatch.setattr(p.transport, name, MagicMock(side_effect=error))
    response = await p.gp.gpconnect(p.nhs, p.saml, request=p.request)
    assert response.status_code == 502
    expected = (
        "ReadError: server closed connection before responding"
        if boundary == "post"
        else f"Transport error (step: gpconnect_request.{step}, exception_type: {type(error).__name__})"
    )
    assert json.loads(response.body) == {"success": False, "error": expected}
    assert p.audits[-1]["action"] == "gpconnect_request"
    assert p.audits[-1]["outcome"] == AuditOutcome.fail
    assert p.audits[-1]["detail"] == {
        "step": f"gpconnect_request.{step}",
        "transport": "direct",
        "exception_type": type(error).__name__,
    }
    assert "fhir.models" not in p.events
    assert not p.published


@pytest.mark.asyncio
@pytest.mark.parametrize("relay", [False, True])
async def test_transport_production_missing_base(observed_pipeline, monkeypatch, relay):
    p = observed_pipeline
    monkeypatch.setattr(p.gp, "environment", "prod")
    monkeypatch.setattr(p.gp, "USE_RELAY", relay)
    monkeypatch.setattr(p.gp, "RELAY_BASE_PATH" if relay else "OVER_INTERNET_PATH", None)
    response = await p.gp.gpconnect(p.nhs, p.saml, request=p.request)
    assert response.status_code == 502
    assert json.loads(response.body)["error"] == (
        "Transport error (step: gpconnect_request.transport_configuration, exception_type: ValueError)"
    )
    assert p.audits[-1]["detail"]["transport"] == ("relay" if relay else "direct")
    assert "xhuma.gpconnect.http" not in [s.name for s in p.exporter.get_finished_spans()]
    p.transport.httpx.AsyncClient.assert_not_called()


def test_transport_certificate_paths(monkeypatch, tmp_path):
    from app.gpconnect import get_nhs_cert_paths

    monkeypatch.setenv("NHS_CERT_DIR_EPHEMERAL", str(tmp_path))
    monkeypatch.setenv("ENV", "prod")
    with pytest.raises(FileNotFoundError, match="^Production NHS certificates not found in ephemeral storage$"):
        get_nhs_cert_paths()
    monkeypatch.setenv("ENV", "int")
    assert get_nhs_cert_paths() == tuple(
        f"keys/nhs_certs/{name}" for name in ("client_cert.pem", "client_key.pem", "nhs_bundle.pem")
    )
    # Only the certificate is checked here: missing key/CA are deferred to TLS setup.
    (tmp_path / "client_cert.pem").touch()
    monkeypatch.setenv("ENV", "prod")
    assert get_nhs_cert_paths() == tuple(
        str(tmp_path / name) for name in ("client_cert.pem", "client_key.pem", "nhs_bundle.pem")
    )


def test_transport_tls_configuration(monkeypatch, tmp_path):
    import ssl

    from app.gpconnect import create_nhs_ssl_context

    paths = [str(tmp_path / name) for name in ("cert", "key", "ca")]
    factory = MagicMock()
    monkeypatch.setattr(ssl, "create_default_context", factory)
    for path in paths:
        with pytest.raises(FileNotFoundError, match=f"^NHS Certificate file not found: {path}$"):
            create_nhs_ssl_context(*paths)
        factory.assert_not_called()
        Path(path).touch()
    context = create_nhs_ssl_context(*paths)
    factory.assert_called_once_with(ssl.Purpose.SERVER_AUTH)
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2
    context.load_cert_chain.assert_called_once_with(*paths[:2])
    context.load_verify_locations.assert_called_once_with(paths[2])
    error = ssl.SSLError("synthetic invalid certificate")
    context.load_cert_chain.side_effect = error
    with pytest.raises(ssl.SSLError) as caught:
        create_nhs_ssl_context(*paths)
    assert caught.value is error


@pytest.fixture
def observed_relay(observed_pipeline, monkeypatch):
    """Capture the relay envelope and reconstructed response at existing I/O boundaries."""
    p = observed_pipeline
    settings = importlib.import_module("app.settings")
    monkeypatch.setattr(p.gp, "USE_RELAY", True)
    monkeypatch.setattr(p.gp, "RELAY_BASE_PATH", "https://relay.example.invalid/")
    monkeypatch.setattr(settings, "EXTERNAL_RELAY_URL", "")
    monkeypatch.setattr(settings, "EXTERNAL_RELAY_CLIENT_ID", "synthetic-client")
    monkeypatch.setattr(settings, "EXTERNAL_RELAY_TOKEN", "synthetic-token")
    p.relay_payload = {
        "status_code": "200",
        "body": p.http_response.json(),
        "headers": {"content-encoding": "gzip", "x-upstream": "discard"},
    }
    p.state.relay_hub = SimpleNamespace(send=AsyncMock(side_effect=lambda _: p.relay_payload))
    p.rebuilt = []
    real_response = Response

    def rebuild(*args, **kwargs):
        response = real_response(*args, **kwargs)
        p.rebuilt.append(response)
        return response

    monkeypatch.setattr(p.transport.httpx, "Response", rebuild)
    p.settings = settings
    return p


@pytest.mark.asyncio
@pytest.mark.parametrize("external,token", [(False, False), (True, False), (True, True)])
async def test_transport_relay_success(observed_relay, monkeypatch, external, token):
    p = observed_relay
    if external:
        monkeypatch.setattr(p.settings, "EXTERNAL_RELAY_URL", "https://external.example.invalid///")
        monkeypatch.setattr(p.settings, "EXTERNAL_RELAY_TOKEN", "synthetic-token" if token else "")
        p.transport.httpx.AsyncClient.return_value.post.side_effect = None
        p.transport.httpx.AsyncClient.return_value.post.return_value = Response(200, json=p.relay_payload)
    response = await p.gp.gpconnect(p.nhs, p.saml, request=p.request)
    assert response.status_code == 200
    if external:
        p.transport.httpx.AsyncClient.assert_called_once_with(timeout=httpx.Timeout(75.0))
        call = p.transport.httpx.AsyncClient.return_value.post.call_args
        assert call.args == ("https://external.example.invalid/send/synthetic-client",)
        assert call.kwargs["headers"] == ({"Authorization": "Bearer synthetic-token"} if token else {})
        envelope = call.kwargs["json"]
        p.state.relay_hub.send.assert_not_awaited()
    else:
        p.transport.httpx.AsyncClient.assert_not_called()
        envelope = p.state.relay_hub.send.call_args.args[0]
    assert set(envelope) == {"request_id", "method", "url", "headers", "body"}
    from uuid import UUID

    assert str(UUID(envelope["request_id"])) == envelope["request_id"]
    # Preserve literal slash concatenation, including the double slash.
    url = "https://relay.example.invalid//fake-gpconnect-endpoint/Patient/$gpc.getstructuredrecord"
    assert envelope["url"] == url
    assert envelope["method"] == "POST"
    assert json.loads(envelope["body"])["parameter"][0]["valueIdentifier"]["value"] == p.nhs
    assert envelope["headers"]["Ssp-To"] == "200000000000"
    assert p.rebuilt[-1].request.method == "POST"
    assert str(p.rebuilt[-1].request.url) == url
    assert p.rebuilt[-1].json() == p.relay_payload["body"]
    assert "content-encoding" not in p.rebuilt[-1].headers
    assert "x-upstream" not in p.rebuilt[-1].headers
    p.transport.create_nhs_ssl_context.assert_not_called()
    spans = [s for s in p.exporter.get_finished_spans() if s.name == "xhuma.gpconnect.http"]
    assert len(spans) == 1
    assert spans[0].attributes["transport"] == "relay"
    assert spans[0].attributes["stage.outcome"] == "ok"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,expected",
    [
        ({"synthetic": True}, '{"synthetic": true}'),
        ([1, "x"], '[1, "x"]'),
        (b"bad\xff", "bad\ufffd"),
        ("text", "text"),
        (None, ""),
        (False, ""),
        (0, ""),
        (42, "42"),
    ],
)
async def test_transport_relay_body_conversion(observed_relay, body, expected):
    p = observed_relay
    p.relay_payload = {"status_code": "418", "body": body, "headers": {"content-encoding": "gzip"}}
    response = await p.gp.gpconnect(p.nhs, p.saml, request=p.request)
    assert response.status_code == 418
    assert json.loads(response.body)["error"] == "Error from GP Connect endpoint 418"
    assert p.rebuilt[-1].text == expected
    assert p.rebuilt[-1].status_code == 418
    assert "content-encoding" not in p.rebuilt[-1].headers
    assert p.audits[-1]["detail"]["step"] == "gpconnect_request.response_status"


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{"body": "missing status"}, {"status_code": 503, "body": "failure"}])
async def test_transport_relay_status_default(observed_relay, payload):
    p = observed_relay
    p.relay_payload = payload
    response = await p.gp.gpconnect(p.nhs, p.saml, request=p.request)
    assert response.status_code == payload.get("status_code", 502)
    assert p.rebuilt[-1].status_code == response.status_code
    assert p.audits[-1]["detail"]["step"] == "gpconnect_request.response_status"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case,step,status,error_type,detail",
    [
        ("missing-hub", "relay_hub_lookup", 404, "HTTPException", "Relay not available in this process"),
        ("hub-failure", "relay_hub_send", 502, "RuntimeError", None),
        ("hub-timeout", "relay_hub_send", 502, "TimeoutError", None),
        ("status", "relay_status", 502, "ValueError", None),
        ("external-failure", "external_relay_status", 502, "HTTPException", "External relay error"),
        ("external-decode", "external_relay_decode", 502, "JSONDecodeError", None),
        ("external-timeout", "external_relay_post", 502, "ReadTimeout", None),
        ("external-entry", "external_relay_client", 502, "RuntimeError", None),
        ("external-exit", "external_relay_client_close", 502, "RuntimeError", None),
    ],
)
async def test_transport_relay_failure_steps(observed_relay, monkeypatch, case, step, status, error_type, detail):
    p = observed_relay
    client = p.transport.httpx.AsyncClient.return_value
    if case.startswith("external"):
        monkeypatch.setattr(p.settings, "EXTERNAL_RELAY_URL", "https://external.example.invalid")
        client.post.side_effect = None
        client.post.return_value = Response(200, json=p.relay_payload)
        if case == "external-failure":
            client.post.return_value = Response(503)
        elif case == "external-decode":
            client.post.return_value = Response(200, text="not JSON")
        elif case == "external-timeout":
            client.post.side_effect = httpx.ReadTimeout("synthetic timeout")
        elif case == "external-entry":
            client.__aenter__.side_effect = RuntimeError("synthetic entry")
        elif case == "external-exit":
            client.__aexit__.side_effect = RuntimeError("synthetic exit")
    elif case == "missing-hub":
        del p.state.relay_hub
    elif case == "hub-failure":
        p.state.relay_hub.send.side_effect = RuntimeError("synthetic failure")
    elif case == "hub-timeout":
        p.state.relay_hub.send.side_effect = TimeoutError("synthetic timeout")
    else:
        p.relay_payload["status_code"] = "invalid"
    response = await p.gp.gpconnect(p.nhs, p.saml, request=p.request)
    assert response.status_code == status
    expected = (
        f"Transport error: {detail}"
        if detail
        else f"Transport error (step: gpconnect_request.{step}, exception_type: {error_type})"
    )
    assert json.loads(response.body) == {"success": False, "error": expected}
    assert p.audits[-1]["action"] == "gpconnect_request"
    assert p.audits[-1]["outcome"] == AuditOutcome.fail
    assert p.audits[-1]["detail"] == {
        "step": f"gpconnect_request.{step}",
        "transport": "relay",
        "exception_type": error_type,
    }
    assert not p.published
    assert "fhir.models" not in p.events


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["direct", "hub", "external"])
async def test_transport_cancellation_propagates(observed_relay, monkeypatch, mode):
    import asyncio

    p = observed_relay
    cancelled = asyncio.CancelledError("synthetic cancellation")
    client = p.transport.httpx.AsyncClient.return_value
    if mode == "direct":
        monkeypatch.setattr(p.gp, "USE_RELAY", False)
        client.post.side_effect = cancelled
    elif mode == "external":
        monkeypatch.setattr(p.settings, "EXTERNAL_RELAY_URL", "https://external.example.invalid")
        client.post.side_effect = cancelled
    else:
        p.state.relay_hub.send.side_effect = cancelled
    with pytest.raises(asyncio.CancelledError) as caught:
        await p.gp.gpconnect(p.nhs, p.saml, request=p.request)
    assert caught.value is cancelled
    assert p.audits[-1]["action"] == "sds_self_lookup"
    assert not p.published
    if mode != "hub":
        client.__aexit__.assert_awaited_once()
        assert client.__aexit__.call_args.args[1] is cancelled


@pytest.mark.asyncio
async def test_transport_overlapping_requests_keep_failure_steps_local(observed_pipeline, monkeypatch):
    import asyncio

    p = observed_pipeline
    entered = asyncio.Event()
    release = asyncio.Event()
    first = AsyncMock()
    second = AsyncMock()
    first.__aenter__.return_value = first
    second.__aenter__.return_value = second

    async def suspended_post(*args, **kwargs):
        entered.set()
        await release.wait()
        raise RuntimeError("synthetic first failure")

    first.post.side_effect = suspended_post
    second.post.return_value = p.http_response
    second.__aexit__.side_effect = RuntimeError("synthetic second failure")
    monkeypatch.setattr(p.transport.httpx, "AsyncClient", MagicMock(side_effect=[first, second]))
    task = asyncio.create_task(p.gp.gpconnect(p.nhs, p.saml, request=p.request))
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        second_response = await p.gp.gpconnect(p.nhs, p.saml, request=p.request)
    finally:
        release.set()
        first_response = await asyncio.wait_for(task, timeout=2)
    assert json.loads(second_response.body)["error"] == (
        "Transport error (step: gpconnect_request.http_client_close, exception_type: RuntimeError)"
    )
    assert json.loads(first_response.body)["error"] == (
        "Transport error (step: gpconnect_request.http_post, exception_type: RuntimeError)"
    )
    failures = [a for a in p.audits if a["action"] == "gpconnect_request"]
    assert [a["detail"]["step"] for a in failures] == [
        "gpconnect_request.http_client_close",
        "gpconnect_request.http_post",
    ]
    assert len({a["request_id"] for a in failures}) == 2
    assert not p.published


@pytest.mark.asyncio
async def test_document_first_comment_and_index_identity(observed_pipeline, monkeypatch, caplog):
    p = observed_pipeline
    entries = [{"fhir_comments": ["first"]}, {"fhir_comments": ["second"]}]
    p.http_response = Response(200, json={"resourceType": "Bundle", "type": "collection", "entry": entries})
    first = SimpleNamespace(resource_type="Patient", id="same")
    last = SimpleNamespace(resource_type="Patient", id="same")
    partial = SimpleNamespace(resource_type="Patient", id="partial")
    model = SimpleNamespace(
        entry=[
            SimpleNamespace(resource=first, fullUrl="urn:first"),
            SimpleNamespace(resource=last, fullUrl="urn:shared"),
            SimpleNamespace(resource=first, fullUrl="urn:shared"),
            SimpleNamespace(resource=partial),  # Reference insertion survives missing fullUrl.
            SimpleNamespace(resource=None),
        ]
    )

    # The third entry overwrites both reference and fullUrl; objects are not copied.
    def build(payload):
        assert payload["entry"] == [entries[1]]
        return model

    async def convert(actual, index):
        assert actual is model
        assert index == {"Patient/same": first, "Patient/partial": partial, "urn:first": first, "urn:shared": first}
        assert all(index[key] is first for key in ("Patient/same", "urn:first", "urn:shared"))
        return {"ClinicalDocument": {}}

    monkeypatch.setattr(p.document, "bundle", SimpleNamespace(Bundle=build))
    monkeypatch.setattr(p.document, "convert_bundle", convert)
    response = await p.gp.gpconnect(p.nhs, p.saml, request=p.request)
    assert response.status_code == 200
    assert (
        caplog.text.count("Skipping FHIR resource (step: fhir_index.resource_index, exception_type: AttributeError)")
        == 2
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "boundary,step",
    [
        ("redis.enter", "pipeline_open"),
        ("redis.setex", "queue_patient_document"),
        ("redis.hset", "queue_document_content"),
        ("redis.expire", "queue_document_expiry"),
    ],
)
async def test_document_publication_queue_failure(observed_pipeline, boundary, step):
    p = observed_pipeline
    p.failures[boundary] = RuntimeError("synthetic failure")
    response = await p.gp.gpconnect(p.nhs, p.saml, request=p.request)
    assert response.status_code == 500
    assert (
        json.loads(response.body)["error"]
        == f"GP Connect failed (step: ccda_cache.{step}, exception_type: RuntimeError)"
    )
    assert p.audits[-1]["action"] == "store_ccda"
    assert p.audits[-1]["outcome"] == AuditOutcome.ok
    assert "redis.execute" not in p.events
    assert not p.published


@pytest.mark.asyncio
async def test_document_conversion_http_exception(observed_pipeline):
    p = observed_pipeline
    p.failures["convert"] = HTTPException(409, "synthetic conversion detail")
    response = await p.gp.gpconnect(p.nhs, p.saml, request=p.request)
    assert response.status_code == 409
    assert json.loads(response.body)["error"] == "Failed to convert FHIR Bundle to CCDA: synthetic conversion detail"
    assert p.audits[-1]["action"] == "gpconnect_request"
    assert p.audits[-1]["outcome"] == AuditOutcome.ok
    assert not p.published


@pytest.mark.asyncio
@pytest.mark.parametrize("xml_fails", [False, True])
async def test_document_debug_file_order(observed_pipeline, monkeypatch, tmp_path, xml_fails):
    p = observed_pipeline
    directory = tmp_path / "debug"
    directory.mkdir()
    real_open = open

    def debug_open(path, *args, **kwargs):
        name = Path(path).name
        p.note(f"file:{name}")
        if name.endswith(".xml") and xml_fails:
            raise OSError("synthetic write failure")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(p.gp, "open", debug_open, raising=False)
    monkeypatch.setattr(p.document, "open", debug_open, raising=False)
    response = await p.gp._fetch_gpconnect_record(p.nhs, p.saml, log_dir=str(directory), request=p.request)
    assert p.events.index("inject") < p.events.index("file:request_headers.json") < p.events.index("http.post")
    assert (
        p.events.index("http.response")
        < p.events.index("file:200_response.json")
        < p.events.index("audit:gpconnect_request:ok")
    )
    assert p.events.index("convert") < p.events.index(f"file:{p.nhs}.xml")
    if xml_fails:
        assert response.status_code == 500
        assert (
            json.loads(response.body)["error"]
            == "GP Connect failed (step: ccda_serialize.xml_debug_file, exception_type: OSError)"
        )
        assert "serialize" not in p.events
        assert not p.published
    else:
        assert response.status_code == 200
        assert p.events.index(f"file:{p.nhs}.xml") < p.events.index("serialize")


@pytest.mark.asyncio
@pytest.mark.parametrize("party,asid", [("FAKE-PARTY-KEY", "200000000000"), (" party ", " asid "), (7, 9)])
async def test_routing_permissive_identifiers_and_address(observed_pipeline, monkeypatch, party, asid):
    p = observed_pipeline
    endpoint = p.endpoint["entry"][0]["resource"]
    device = p.device["entry"][0]["resource"]
    del endpoint["resourceType"]
    device["resourceType"] = ""
    endpoint["address"] = "  relative-endpoint  "
    endpoint["status"] = "inactive"
    endpoint["identifier"] = [{"system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey", "value": party}] * 2
    device["identifier"] = [{"system": "https://fhir.nhs.uk/Id/nhsSpineASID", "value": asid}] * 2
    for payload in (p.endpoint, p.device):
        payload.update(total=99, link=[{"relation": "next", "url": "https://unused.example.invalid"}])
    sds = AsyncMock(side_effect=[p.endpoint, p.device])
    monkeypatch.setattr(p.gp, "sds_trace", sds)
    response = await p.gp.gpconnect(p.nhs, p.saml, request=p.request)
    assert response.status_code == 200
    assert [call.kwargs for call in sds.await_args_list] == [{"endpoint": True}, {"mhsparty": party}]
    assert p.sent.headers["Ssp-To"] == asid
    assert p.sent.url == "https://proxy.example.invalid/  relative-endpoint  /Patient/$gpc.getstructuredrecord"
    assert (
        jwt.decode(p.sent.headers["Authorization"].split()[1], options={"verify_signature": False})["aud"]
        == "  relative-endpoint  "
    )
    assert [(a["action"], a["outcome"].value) for a in p.audits][1:3] == [
        ("fhir_endpoint_trace", "ok"),
        ("sds_trace", "ok"),
    ]
