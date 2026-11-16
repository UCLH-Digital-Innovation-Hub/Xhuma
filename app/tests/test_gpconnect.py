import base64
import importlib
import json
import logging
import xml.etree.ElementTree as ET
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from httpx import Response
from opentelemetry import trace
from opentelemetry.propagate import extract
from opentelemetry.trace import StatusCode

from app.audit.audit import AuditFailureException
from app.audit.models import AuditOutcome
from app.gpconnect import _fetch_gpconnect_record, gpconnect
from app.tests.configure_tests import get_nhs_ids, load_bundle, load_pds
from app.tests.fixtures.saml_attributes import saml
from app.tests.gpconnect_fixtures import observed_pipeline as observed_pipeline
from app.tests.gpconnect_fixtures import observed_relay as observed_relay
from app.tests.gpconnect_helpers import fake_sds_device_trace, fake_sds_endpoint_trace, get_mock_request

pytest_plugins = ("pytest_asyncio",)
pytestmark = pytest.mark.usefixtures("mock_self_issuer")


@pytest.mark.asyncio
@pytest.mark.parametrize("nhsno", get_nhs_ids())
@patch("app.audit.audit.attempt_audit", new_callable=AsyncMock)
@patch("app.gpconnect_stages.document.convert_bundle", new_callable=AsyncMock)
@patch("app.gpconnect_stages.document.base64_xml")
@patch("app.gpconnect_stages.document.redis_client.pipeline")
@patch("app.gpconnect_stages.transport.create_nhs_ssl_context")
@patch("app.gpconnect_stages.transport.httpx.AsyncClient")
@patch("app.gpconnect_stages.routing.sds_trace", new_callable=AsyncMock)
@patch("app.gpconnect_stages.patient.lookup_patient", new_callable=AsyncMock)
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
    pipeline.set.assert_called_once_with(str(nhsno), body["document_id"], ex=timedelta(hours=4))
    pipeline.hset.assert_called_once_with(
        document_key, mapping={"nhs_number": str(nhsno), "content": "mocked_base64_doc"}
    )
    pipeline.expire.assert_called_once_with(document_key, timedelta(hours=4))
    mock_redis_pipeline.return_value.execute.assert_awaited_once()
    mock_redis_pipeline.return_value.__aexit__.assert_awaited_once()


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
        patch("app.gpconnect_stages.patient.lookup_patient", new_callable=AsyncMock, return_value=patient),
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
        patch("app.gpconnect_stages.patient.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect_stages.routing.sds_trace", new_callable=AsyncMock) as sds,
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
async def test_serialization_failure_returns_caller_error_with_step():
    with (
        patch("app.gpconnect_stages.patient.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect_stages.routing.sds_trace", new_callable=AsyncMock) as sds,
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
        patch("app.gpconnect_stages.patient.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect_stages.routing.sds_trace", new_callable=AsyncMock) as sds,
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
        patch("app.gpconnect_stages.patient.lookup_patient", new_callable=AsyncMock, return_value=pds),
        patch("app.gpconnect_stages.routing.sds_trace", new_callable=AsyncMock) as sds,
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
        "redis.set",
        "redis.hset",
        "redis.expire",
        "redis.execute",
        "redis.exit",
        "json.response",
    ]
    body = json.loads(response.body)
    document_id = body["document_id"]
    assert body == {"success": True, "document_id": document_id}
    assert [command[0] for command in p.commands] == ["set", "hset", "expire"]
    assert p.commands[0] == ("set", p.nhs, document_id, timedelta(hours=1.5))
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
    assert p.commands[0][3] == p.commands[2][2] == ttl
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
    assert p.commands[0][2] == document_id
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
@pytest.mark.parametrize(
    "boundary,step",
    [
        ("redis.enter", "pipeline_open"),
        ("redis.set", "queue_patient_document"),
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
    monkeypatch.setattr(p.request_stage, "open", debug_open, raising=False)
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
    monkeypatch.setattr(p.routing, "sds_trace", sds)
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


@pytest.mark.asyncio
async def test_request_consumer_chain_and_unsigned_claims(observed_pipeline, monkeypatch):
    p = observed_pipeline
    pds = importlib.import_module("app.pds.pds")
    monkeypatch.setenv("XHUMA_ODS_CODE", " synthetic-manufacturer ")
    monkeypatch.setenv("ORG_CODE", " synthetic-care ")
    monkeypatch.setenv("XHUMA_MHS_PARTY_KEY", "synthetic-party")
    device = {
        "resourceType": "Device",
        "identifier": [
            {"system": "https://fhir.nhs.uk/Id/nhsSpineASID", "value": "synthetic-consumer-asid"},
            {"system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey", "value": "synthetic-party"},
        ],
    }
    endpoint = {
        "resourceType": "Endpoint",
        "status": "active",
        "address": "https://consumer.example.invalid/spine",
        "identifier": [device["identifier"][1]],
    }
    sds = AsyncMock(
        side_effect=[
            {"resourceType": "Bundle", "entry": [{"resource": device}]},
            {"resourceType": "Bundle", "entry": [{"resource": endpoint}, {"resource": endpoint}]},
        ]
    )
    monkeypatch.setattr(pds, "sds_trace", sds)
    monkeypatch.setattr(p.request_stage, "lookup_self_issuer", pds.lookup_self_issuer)
    response = await p.gp.gpconnect(p.nhs, p.saml, request=p.request)
    assert response.status_code == 200
    assert [(c.args, c.kwargs) for c in sds.await_args_list] == [
        (("SYNTHETIC-CARE",), {"manufacturer_ods": "SYNTHETIC-MANUFACTURER"}),
        (("SYNTHETIC-MANUFACTURER",), {"endpoint": True, "mhsparty": "synthetic-party", "interaction_id": None}),
    ]
    token = p.sent.headers["Authorization"].removeprefix("Bearer ")
    assert jwt.get_unverified_header(token) == {"alg": "none", "typ": "JWT"}
    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["iss"] == endpoint["address"]
    assert claims["aud"] == "fake-gpconnect-endpoint"
    assert claims["sub"] == p.saml.subject_id
    assert claims["exp"] - claims["iat"] == 300
    assert claims["reason_for_request"] == "directcare"
    assert claims["requested_scope"] == "patient/*.read"
    assert claims["requesting_organization"]["name"] == p.saml.organization
    assert claims["requesting_organization"]["identifier"][1]["value"] == p.saml.organization_id
    assert claims["requesting_practitioner"]["name"] == [{"family": "Synthetic", "given": ["Clinician"]}]
    assert claims["requesting_practitioner"]["identifier"][-1] == {
        "system": "synthetic-system",
        "value": "synthetic-role",
    }
    assert [i["value"] for i in claims["requesting_practitioner"]["identifier"][:2]] == ["UNK", "UNK"]
    assert "purpose_of_use" not in claims
    assert p.sent.headers["Ssp-From"] == "synthetic-consumer-asid"
    assert (
        p.sent.headers["Ssp-InteractionID"]
        == "urn:nhs:names:services:gpconnect:fhir:operation:gpc.getstructuredrecord-1"
    )
    assert p.sent.headers["accept"] == p.sent.headers["Content-Type"] == "application/fhir+json"
    assert p.sent.headers["Ssp-TraceID"] != json.loads(response.body)["document_id"]


@pytest.mark.asyncio
async def test_request_reads_facade_parameters_after_identity_lookup(observed_pipeline, monkeypatch):
    p = observed_pipeline
    replacement = [{"name": "synthetic-refresh", "valueBoolean": True}]

    async def issuer():
        monkeypatch.setattr(p.gp, "GP_CONNECT_PARAMETERS", replacement)
        return "https://consumer.example.invalid/spine"

    monkeypatch.setattr(p.request_stage, "lookup_self_issuer", issuer)
    response = await p.gp.gpconnect(p.nhs, p.saml, request=p.request)
    assert response.status_code == 200
    assert p.sent.body == {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "patientNHSNumber",
                "valueIdentifier": {"system": "https://fhir.nhs.uk/Id/nhs-number", "value": p.nhs},
            },
            *replacement,
        ],
    }
    assert p.sent.body["parameter"][1] is replacement[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "boundary,step,status,last_action",
    [
        ("jwt", "sds_self_lookup.jwt_creation", 502, "sds_self_lookup"),
        ("headers", "gpconnect_request_build.headers", 500, "sds_self_lookup"),
        ("inject", "gpconnect_request_build.trace_injection", 500, "sds_self_lookup"),
    ],
)
async def test_request_construction_failures(observed_pipeline, monkeypatch, boundary, step, status, last_action):
    p = observed_pipeline
    if boundary == "headers":
        # Supply a token so the environment failure is specifically at SSP headers.
        monkeypatch.setattr(p.request_stage, "create_jwt", lambda *args, **kwargs: "synthetic-token")
        monkeypatch.delenv("ORG_ASID")
    else:
        p.failures[boundary] = RuntimeError("synthetic failure")
    response = await p.gp.gpconnect(p.nhs, p.saml, request=p.request)
    assert response.status_code == status
    prefix = "SDS consumer issuer lookup failed" if boundary == "jwt" else "GP Connect failed"
    error_type = "KeyError" if boundary == "headers" else "RuntimeError"
    assert json.loads(response.body)["error"] == f"{prefix} (step: {step}, exception_type: {error_type})"
    assert p.audits[-1]["action"] == last_action
    assert p.audits[-1]["outcome"] == (AuditOutcome.fail if boundary == "jwt" else AuditOutcome.ok)
    assert "http.post" not in p.events


@pytest.mark.asyncio
@pytest.mark.parametrize("ods", [None, "", " padded-ods ", 17])
async def test_patient_first_entries_and_unvalidated_ods(observed_pipeline, monkeypatch, ods):
    p = observed_pipeline
    p.patient["meta"]["security"] = [{"code": "U"}, {"code": "R"}]
    p.patient["generalPractitioner"] = [{"identifier": {"value": ods}}, {"identifier": {"value": "ignored"}}]
    sds = AsyncMock(side_effect=HTTPException(503, "synthetic stop"))
    monkeypatch.setattr(p.routing, "sds_trace", sds)
    response = await p.gp.gpconnect(p.nhs, p.saml, request=p.request)
    assert response.status_code == 503
    sds.assert_awaited_once_with(ods, endpoint=True)
    assert [(a["action"], a["outcome"].value) for a in p.audits] == [
        ("pds_lookup", "ok"),
        ("fhir_endpoint_trace", "fail"),
    ]


@pytest.mark.asyncio
async def test_patient_cache_hit_audit_failure_stops_routing(observed_pipeline, caplog):
    p = observed_pipeline
    p.fail_audit = "pds_lookup"
    with pytest.raises(AuditFailureException, match="^GP Connect audit persistence failed$"):
        await p.gp.gpconnect(p.nhs, p.saml, request=p.request)
    assert p.events == ["nhs.validate", "pds", "audit:pds_lookup:ok"]
    assert "GP Connect failed (step: pds_lookup.lookup_patient, exception_type: AuditFailureException)" in caplog.text
    assert not p.published


@pytest.mark.asyncio
async def test_pipeline_overlapping_misses_generate_independent_documents(observed_pipeline):
    import asyncio

    p = observed_pipeline
    both_entered = asyncio.Event()
    trace_ids = []

    async def post(url, *, json, headers):
        trace_ids.append(headers["Ssp-TraceID"])
        if len(trace_ids) == 2:
            both_entered.set()
        await both_entered.wait()
        return p.http_response

    p.transport.httpx.AsyncClient.return_value.post.side_effect = post
    responses = await asyncio.wait_for(
        asyncio.gather(
            p.gp.gpconnect(p.nhs, p.saml, request=p.request),
            p.gp.gpconnect(p.nhs, p.saml, request=p.request),
        ),
        timeout=2,
    )
    assert [response.status_code for response in responses] == [200, 200]
    documents = {json.loads(response.body)["document_id"] for response in responses}
    assert len(documents) == len(set(trace_ids)) == 2
    assert documents.isdisjoint(trace_ids)
    pointers = [command for command in p.commands if command[0] == "set"]
    assert [command[1] for command in pointers] == [p.nhs, p.nhs]
    assert {command[2] for command in pointers} == documents
    assert {command[1] for command in p.commands if command[0] == "hset"} == {
        f"document:{document}" for document in documents
    }
    stores = [event for event in p.persisted if event.event.action == "store_ccda"]
    assert {event.event.data_refs.document_id for event in stores} == documents
    assert all(event.event.outcome == AuditOutcome.ok for event in stores)
