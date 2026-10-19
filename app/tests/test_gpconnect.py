import importlib
import json
import logging
import xml.etree.ElementTree as ET
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import jwt
import pytest
from httpx import Response

from app.audit.audit import AuditFailureException
from app.audit.models import AuditOutcome
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
        "entry": [
            {
                "resource": {
                    "identifier": [
                        {
                            "system": "https://fhir.nhs.uk/Id/nhsSpineASID",
                            "value": "200000000000",
                        },
                        {
                            "system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey",
                            "value": "FAKE-PARTY-KEY",
                        },
                    ]
                }
            }
        ]
    }


def fake_sds_endpoint_trace():
    return {
        "entry": [
            {
                "resource": {
                    "address": "fake-gpconnect-endpoint",
                    "identifier": [
                        {
                            "system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey",
                            "value": "FAKE-PARTY-KEY",
                        }
                    ],
                }
            }
        ]
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("nhsno", get_nhs_ids())
@patch("app.gpconnect.attempt_audit", new_callable=AsyncMock)
@patch("app.gpconnect.convert_bundle", new_callable=AsyncMock)
@patch("app.gpconnect.base64_xml")
@patch("app.gpconnect.redis_client.pipeline")
@patch("app.gpconnect.create_nhs_ssl_context")
@patch("app.gpconnect.httpx.AsyncClient")
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
        patch("app.gpconnect.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.gpconnect.httpx.AsyncClient") as client,
        patch("app.gpconnect.redis_client.pipeline") as cache,
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
@patch("app.gpconnect.attempt_audit", new_callable=AsyncMock)
@patch("app.gpconnect.lookup_patient", new_callable=AsyncMock)
async def test_gpconnect_returns_400_for_invalid_nhs_number(mock_lookup_patient, mock_attempt_audit):
    result = await gpconnect(1234567890, saml_attrs=saml, request=get_mock_request())
    body = json.loads(result.body)

    assert result.status_code == 400
    assert body["success"] is False
    assert "not a valid NHS number" in body["error"]

    mock_lookup_patient.assert_not_called()


@pytest.mark.asyncio
@patch("app.gpconnect.attempt_audit", new_callable=AsyncMock)
@patch("app.gpconnect.lookup_patient", new_callable=AsyncMock)
async def test_gpconnect_returns_502_when_pds_lookup_fails(mock_lookup_patient, mock_attempt_audit):
    mock_lookup_patient.side_effect = Exception("PDS unavailable")

    result = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())
    body = json.loads(result.body)

    assert result.status_code == 502
    assert body["success"] is False
    assert "PDS lookup failed" in body["error"]


@pytest.mark.asyncio
@patch("app.gpconnect.attempt_audit", new_callable=AsyncMock)
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
@patch("app.gpconnect.attempt_audit", new_callable=AsyncMock)
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
@patch("app.gpconnect.attempt_audit", new_callable=AsyncMock)
@patch("app.gpconnect.sds_trace", new_callable=AsyncMock)
@patch("app.gpconnect.lookup_patient", new_callable=AsyncMock)
@patch("app.gpconnect.record_application_failure")
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
        patch("app.gpconnect.attempt_audit", new_callable=AsyncMock) as audit,
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
        patch("app.gpconnect.create_nhs_ssl_context"),
        patch("app.gpconnect.httpx.AsyncClient") as factory,
        patch("app.gpconnect.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.gpconnect.record_application_failure") as telemetry,
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
        patch("app.gpconnect.create_nhs_ssl_context"),
        patch("app.gpconnect.httpx.AsyncClient") as factory,
        patch("app.gpconnect.attempt_audit", new_callable=AsyncMock) as audit,
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
        patch("app.gpconnect.attempt_audit", new_callable=AsyncMock),
        patch("app.gpconnect.create_nhs_ssl_context"),
        patch("app.gpconnect.httpx.AsyncClient") as factory,
        patch("app.gpconnect.convert_bundle", new_callable=AsyncMock, return_value={"ClinicalDocument": {}}),
        patch("app.gpconnect.base64_xml", side_effect=ValueError("PRIVATE_INTERNAL_ERROR")),
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
        pytest.param({"entry": []}, "IndexError", id="empty-entry"),
        pytest.param({"entry": [{"resource": None}]}, "AttributeError", id="null-resource"),
        pytest.param({"entry": [{"resource": {"identifier": None}}]}, "TypeError", id="null-identifiers"),
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
        device_trace = {"entry": [{"resource": {"identifier": [SensitiveIdentifier()]}}]}

    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.gpconnect.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.gpconnect.record_application_failure") as telemetry,
        patch("app.gpconnect.httpx.AsyncClient") as client,
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
        pytest.param({"entry": None}, "HTTPException", id="null-entry"),
        pytest.param({"entry": []}, "HTTPException", id="empty-entry"),
        pytest.param({"entry": [None]}, "TypeError", id="null-first-entry"),
        pytest.param({"entry": [{}]}, "KeyError", id="missing-resource"),
        pytest.param({"entry": [{"resource": None}]}, "TypeError", id="null-resource"),
        pytest.param({"entry": [{"resource": {}}]}, "KeyError", id="missing-address"),
        pytest.param({"entry": [{"resource": {"address": None}}]}, "ValueError", id="null-address"),
        pytest.param({"entry": [{"resource": {"address": ""}}]}, "ValueError", id="empty-address"),
        pytest.param({"entry": [{"resource": {"address": " \t\n"}}]}, "ValueError", id="blank-address"),
        pytest.param({"entry": [{"resource": {"address": 123}}]}, "ValueError", id="nonstring-address"),
    ],
)
async def test_invalid_endpoint_trace_is_audited_without_downstream_request(
    mock_self_issuer, endpoint_trace, exception_type, wire
):

    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.gpconnect.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.gpconnect.httpx.AsyncClient") as client,
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
        patch("app.gpconnect.attempt_audit", new_callable=AsyncMock, side_effect=fail_audit) as audit,
        patch("app.gpconnect.record_application_failure") as telemetry,
        patch("app.gpconnect.httpx.AsyncClient") as client,
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
async def test_endpoint_trace_uses_first_entry_address():
    first_address = "https://first-gpconnect-endpoint.example/fhir"
    endpoint_trace = {
        "entry": [
            {
                "resource": {
                    "address": first_address,
                    "identifier": [
                        {
                            "system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey",
                            "value": "FAKE-PARTY-KEY",
                        }
                    ],
                }
            },
            {"resource": {"address": "https://second-gpconnect-endpoint.example/fhir"}},
        ]
    }

    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.gpconnect.attempt_audit", new_callable=AsyncMock),
        patch("app.gpconnect.create_nhs_ssl_context"),
        patch("app.gpconnect.httpx.AsyncClient") as client,
    ):
        sds.side_effect = [endpoint_trace, fake_sds_device_trace()]
        post = client.return_value.__aenter__.return_value.post
        post.return_value = Response(503, content="Service unavailable")

        response = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())

    assert response.status_code == 503
    post.assert_awaited_once()
    token = post.call_args.kwargs["headers"]["Authorization"].removeprefix("Bearer ")
    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["aud"] == first_address


@pytest.mark.asyncio
@pytest.mark.parametrize("audit_fails", [False, True])
async def test_endpoint_lookup_failure_is_audited_without_downstream_request(mock_self_issuer, audit_fails):

    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.gpconnect.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.gpconnect.httpx.AsyncClient") as client,
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
@patch("app.gpconnect.attempt_audit", new_callable=AsyncMock)
@patch("app.gpconnect.record_application_failure")
@patch("app.gpconnect.httpx.AsyncClient")
@patch("app.gpconnect.create_nhs_ssl_context")
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
        patch("app.gpconnect.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.gpconnect.record_application_failure") as telemetry,
        patch("app.gpconnect.convert_bundle", new_callable=AsyncMock) as convert,
        patch("app.gpconnect.redis_client.pipeline") as cache,
        patch("app.gpconnect.create_nhs_ssl_context"),
        patch("app.gpconnect.httpx.AsyncClient") as client,
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
@patch("app.gpconnect.attempt_audit", new_callable=AsyncMock)
@patch("app.gpconnect.httpx.AsyncClient")
@patch("app.gpconnect.create_nhs_ssl_context")
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
@patch("app.gpconnect.attempt_audit", new_callable=AsyncMock)
@patch("app.gpconnect.httpx.AsyncClient")
@patch("app.gpconnect.create_nhs_ssl_context")
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
@patch("app.gpconnect.attempt_audit", new_callable=AsyncMock)
@patch("app.gpconnect.record_application_failure")
@patch("app.gpconnect.httpx.AsyncClient")
@patch("app.gpconnect.create_nhs_ssl_context")
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
        patch("app.gpconnect.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.gpconnect.record_application_failure") as mock_record_failure,
        patch("app.gpconnect.httpx.AsyncClient") as client,
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
        patch("app.gpconnect.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.gpconnect.record_application_failure") as mock_record_failure,
        patch("app.gpconnect.httpx.AsyncClient") as client,
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
        patch("app.gpconnect.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.gpconnect.record_application_failure") as mock_record_failure,
        patch("app.gpconnect.httpx.AsyncClient") as client,
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
        patch("app.gpconnect.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.gpconnect.record_application_failure") as mock_record_failure,
        patch("app.gpconnect.httpx.AsyncClient") as client,
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
        "entry": [
            {
                "resource": {
                    "address": "fake-endpoint-address",
                    "identifier": [{"system": "https://fhir.nhs.uk/Id/other", "value": "other"}],
                }
            }
        ]
    }
    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.gpconnect.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.gpconnect.record_application_failure"),
        patch("app.gpconnect.httpx.AsyncClient") as client,
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
    assert not any(call.kwargs["action"] == "sds_trace" for call in audit.await_args_list)
    mock_self_issuer.assert_not_awaited()
    client.assert_not_called()


@pytest.mark.asyncio
async def test_gpconnect_ambiguous_party_key_fails_closed(mock_self_issuer):
    """Endpoint resource with multiple nhsMhsPartyKey values must fail closed with 502."""
    endpoint_with_ambiguous_party_key = {
        "entry": [
            {
                "resource": {
                    "address": "fake-endpoint-address",
                    "identifier": [
                        {"system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey", "value": "KEY-1"},
                        {"system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey", "value": "KEY-2"},
                    ],
                }
            }
        ]
    }
    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.gpconnect.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.gpconnect.record_application_failure"),
        patch("app.gpconnect.httpx.AsyncClient") as client,
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
        patch("app.gpconnect.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.gpconnect.record_application_failure"),
        patch("app.gpconnect.httpx.AsyncClient") as client,
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
        patch("app.gpconnect.attempt_audit", new_callable=AsyncMock),
        patch("app.gpconnect.record_application_failure"),
        patch("app.gpconnect.httpx.AsyncClient"),
        caplog.at_level(logging.DEBUG),
    ):
        if sds_mock:
            sds.side_effect = sds_mock

        response = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())
        assert response.status_code in (500, 502)

        # Assert raw NHS number is strictly absent from all logged records
        assert "9690937278" not in caplog.text
        assert "NHS=" not in caplog.text
