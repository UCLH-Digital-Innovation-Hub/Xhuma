import json
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from httpx import Response

from app.gpconnect import gpconnect
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
    return {"entry": [{"resource": {"address": "fake-gpconnect-endpoint"}}]}


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
    # 1. Device trace
    # 2. Endpoint trace
    mock_sds_trace.side_effect = [
        fake_sds_device_trace(),
        fake_sds_endpoint_trace(),
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
    mock_client.post.assert_called_once()
    sent_headers = mock_client.post.call_args.kwargs["headers"]
    claims = jwt.decode(sent_headers["Authorization"].removeprefix("Bearer "), options={"verify_signature": False})
    assert claims["iss"] == "https://consumer.test/spine"
    assert claims["aud"] == "fake-gpconnect-endpoint"
    assert sent_headers["Ssp-From"] == "123"
    mock_convert_bundle.assert_called_once()
    mock_base64_xml.assert_called_once()
    mock_redis_pipeline.return_value.execute.assert_awaited_once()
    mock_redis_pipeline.return_value.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("audit_fails", [False, True])
async def test_self_lookup_failure_is_audited_without_downstream_request(mock_self_issuer, audit_fails):
    from app.audit.audit import AuditFailureException
    from app.audit.models import AuditOutcome

    mock_self_issuer.side_effect = ValueError("SYNTHETIC_SENSITIVE_LOOKUP_ERROR")
    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.gpconnect.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.gpconnect.httpx.AsyncClient") as client,
        patch("app.gpconnect.redis_client.pipeline") as cache,
    ):
        sds.side_effect = [fake_sds_device_trace(), fake_sds_endpoint_trace()]
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
            assert json.loads(response.body) == {"success": False, "error": "SDS consumer issuer lookup failed"}
        assert audit.await_args.kwargs["action"] == "sds_self_lookup"
        assert audit.await_args.kwargs["outcome"] == AuditOutcome.fail
        assert audit.await_args.kwargs["detail"] == {"exception_type": "ValueError"}
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
    assert body["error"] == "SDS Device resolution failed for ODS code E82665"
    assert "SDS unavailable" not in body["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "device_trace",
    [
        pytest.param(None, id="null-response"),
        pytest.param({}, id="missing-entry"),
        pytest.param({"entry": None}, id="null-entry"),
        pytest.param({"entry": []}, id="empty-entry"),
        pytest.param({"entry": [None]}, id="null-first-entry"),
        pytest.param({"entry": [{}]}, id="missing-resource"),
        pytest.param({"entry": [{"resource": None}]}, id="null-resource"),
        pytest.param({"entry": [{"resource": {}}]}, id="missing-identifier"),
        pytest.param({"entry": [{"resource": {"identifier": None}}]}, id="null-identifier"),
        pytest.param({"entry": [{"resource": {"identifier": []}}]}, id="empty-identifier"),
        pytest.param(
            {
                "entry": [
                    {
                        "resource": {
                            "identifier": [
                                {
                                    "system": "https://fhir.nhs.uk/Id/nhsSpineASID",
                                    "value": "200000000000",
                                }
                            ]
                        }
                    }
                ]
            },
            id="missing-mhspartykey",
        ),
        pytest.param(
            {
                "entry": [
                    {
                        "resource": {
                            "identifier": [
                                {
                                    "system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey",
                                    "value": "FAKE-PARTY-KEY",
                                }
                            ]
                        }
                    }
                ]
            },
            id="missing-asid",
        ),
        pytest.param(
            {
                "entry": [
                    {
                        "resource": {
                            "identifier": [
                                {
                                    "system": "https://fhir.nhs.uk/Id/nhsSpineASID",
                                    "value": "",
                                },
                                {
                                    "system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey",
                                    "value": "FAKE-PARTY-KEY",
                                },
                            ]
                        }
                    }
                ]
            },
            id="empty-asid",
        ),
        pytest.param(
            {
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
                                    "value": " \t\n",
                                },
                            ]
                        }
                    }
                ]
            },
            id="blank-mhspartykey",
        ),
        pytest.param(
            {
                "entry": [
                    {
                        "resource": {
                            "identifier": [
                                {
                                    "system": "https://fhir.nhs.uk/Id/nhsSpineASID",
                                    "value": 12345,
                                },
                                {
                                    "system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey",
                                    "value": "FAKE-PARTY-KEY",
                                },
                            ]
                        }
                    }
                ]
            },
            id="nonstring-asid",
        ),
    ],
)
async def test_invalid_device_trace_is_audited_without_endpoint_lookup(mock_self_issuer, device_trace):
    from app.audit.models import AuditOutcome

    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.gpconnect.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.gpconnect.httpx.AsyncClient") as client,
    ):
        sds.side_effect = [device_trace, fake_sds_endpoint_trace()]

        response = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())

        assert response.status_code == 502
        body = json.loads(response.body)
        assert body["success"] is False
        assert body["error"] == "SDS Device resolution failed for ODS code E82665"
        assert "list index out of range" not in body["error"]
        assert "IndexError" not in body["error"]

        device_audits = [call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "sds_trace"]
        assert len(device_audits) == 1
        assert device_audits[0]["outcome"] == AuditOutcome.fail
        assert device_audits[0]["error_code"] == "502"
        assert device_audits[0]["detail"]["stage"] == "sds_device"
        assert isinstance(device_audits[0]["detail"]["exception_type"], str)

        # Prove no sds_trace success audit was emitted
        assert not any(
            call.kwargs["action"] == "sds_trace" and call.kwargs["outcome"] == AuditOutcome.ok
            for call in audit.await_args_list
        )

        # Prove Endpoint lookup was never attempted (sds called only once)
        assert sds.call_count == 1
        assert not any(call.kwargs["action"] == "fhir_endpoint_trace" for call in audit.await_args_list)

        mock_self_issuer.assert_not_awaited()
        client.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("audit_fails", [False, True])
async def test_device_lookup_failure_is_audited_without_endpoint_lookup(mock_self_issuer, audit_fails):
    from app.audit.audit import AuditFailureException
    from app.audit.models import AuditOutcome

    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.gpconnect.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.gpconnect.httpx.AsyncClient") as client,
    ):
        sds.side_effect = [RuntimeError("SDS unavailable")]
        if audit_fails:

            async def fail_device_audit(**kwargs):
                if kwargs["action"] == "sds_trace" and kwargs["outcome"] == AuditOutcome.fail:
                    raise AuditFailureException("Audit unavailable")

            audit.side_effect = fail_device_audit
            with pytest.raises(AuditFailureException):
                await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())
        else:
            response = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())
            assert response.status_code == 502
            assert json.loads(response.body)["error"] == "SDS Device resolution failed for ODS code E82665"
            assert "SDS unavailable" not in json.loads(response.body)["error"]

        device_audits = [call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "sds_trace"]
        assert len(device_audits) == 1
        assert device_audits[0]["outcome"] == AuditOutcome.fail
        assert device_audits[0]["error_code"] == "502"
        assert device_audits[0]["detail"] == {"stage": "sds_device", "exception_type": "RuntimeError"}
        assert sds.call_count == 1
        mock_self_issuer.assert_not_awaited()
        client.assert_not_called()


@pytest.mark.asyncio
async def test_valid_device_trace_resolves_and_proceeds_to_endpoint(mock_self_issuer):
    from app.audit.models import AuditOutcome

    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.gpconnect.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.gpconnect.create_nhs_ssl_context"),
        patch("app.gpconnect.httpx.AsyncClient") as client,
    ):
        sds.side_effect = [fake_sds_device_trace(), fake_sds_endpoint_trace()]

        mock_response = MagicMock(status_code=200, text='{"resourceType": "Bundle", "type": "searchset", "entry": []}')
        mock_client_instance = AsyncMock()
        mock_client_instance.post.return_value = mock_response
        client.return_value.__aenter__.return_value = mock_client_instance

        with (
            patch("app.gpconnect.convert_bundle", new_callable=AsyncMock) as mock_convert,
            patch("app.gpconnect.base64_xml") as mock_b64,
            patch("app.gpconnect.redis_client.pipeline") as mock_pipeline,
        ):
            mock_convert.return_value = {"ClinicalDocument": {}}
            mock_b64.return_value = "b64"
            mock_pipe = mock_pipeline.return_value
            mock_pipe.__aenter__.return_value = mock_pipe
            mock_pipe.execute = AsyncMock()

            response = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())

            assert response.status_code == 200
            assert json.loads(response.body)["success"] is True

        # Exactly one sds_trace success audit
        device_audits = [call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "sds_trace"]
        assert len(device_audits) == 1
        assert device_audits[0]["outcome"] == AuditOutcome.ok

        # Both Device and Endpoint lookups executed
        assert sds.call_count == 2
        endpoint_audits = [
            call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "fhir_endpoint_trace"
        ]
        assert len(endpoint_audits) == 1
        assert endpoint_audits[0]["outcome"] == AuditOutcome.ok


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint_trace",
    [
        pytest.param(None, id="null-response"),
        pytest.param({}, id="missing-entry"),
        pytest.param({"entry": None}, id="null-entry"),
        pytest.param({"entry": []}, id="empty-entry"),
        pytest.param({"entry": [None]}, id="null-first-entry"),
        pytest.param({"entry": [{}]}, id="missing-resource"),
        pytest.param({"entry": [{"resource": None}]}, id="null-resource"),
        pytest.param({"entry": [{"resource": {}}]}, id="missing-address"),
        pytest.param({"entry": [{"resource": {"address": None}}]}, id="null-address"),
        pytest.param({"entry": [{"resource": {"address": ""}}]}, id="empty-address"),
        pytest.param({"entry": [{"resource": {"address": " \t\n"}}]}, id="blank-address"),
        pytest.param({"entry": [{"resource": {"address": 123}}]}, id="nonstring-address"),
    ],
)
async def test_invalid_endpoint_trace_is_audited_without_downstream_request(mock_self_issuer, endpoint_trace):
    from app.audit.models import AuditOutcome

    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.gpconnect.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.gpconnect.httpx.AsyncClient") as client,
    ):
        sds.side_effect = [fake_sds_device_trace(), endpoint_trace]

        response = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())

        assert response.status_code == 502
        body = json.loads(response.body)
        assert body["success"] is False
        assert body["error"] == "SDS Endpoint resolution failed for ODS code E82665"
        endpoint_audits = [
            call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "fhir_endpoint_trace"
        ]
        assert len(endpoint_audits) == 1
        assert endpoint_audits[0]["outcome"] == AuditOutcome.fail
        assert endpoint_audits[0]["error_code"] == "502"
        assert endpoint_audits[0]["detail"]["stage"] == "sds_endpoint"
        assert isinstance(endpoint_audits[0]["detail"]["exception_type"], str)
        mock_self_issuer.assert_not_awaited()
        client.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("audit_fails", [False, True])
async def test_endpoint_lookup_failure_is_audited_without_downstream_request(mock_self_issuer, audit_fails):
    from app.audit.audit import AuditFailureException
    from app.audit.models import AuditOutcome

    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.gpconnect.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.gpconnect.httpx.AsyncClient") as client,
    ):
        sds.side_effect = [fake_sds_device_trace(), RuntimeError("SDS unavailable")]
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
            assert json.loads(response.body)["error"] == "SDS Endpoint resolution failed for ODS code E82665"
            assert "SDS unavailable" not in json.loads(response.body)["error"]

        endpoint_audits = [
            call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "fhir_endpoint_trace"
        ]
        assert len(endpoint_audits) == 1
        assert endpoint_audits[0]["outcome"] == AuditOutcome.fail
        assert endpoint_audits[0]["error_code"] == "502"
        assert endpoint_audits[0]["detail"] == {"stage": "sds_endpoint", "exception_type": "RuntimeError"}
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
    import httpx

    fake_pds = load_pds(9690937278)
    mock_lookup_patient.return_value = fake_pds
    mock_sds_trace.side_effect = [fake_sds_device_trace(), fake_sds_endpoint_trace()]

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
    from pathlib import Path

    from app.audit.audit import AuditFailureException
    from app.audit.models import AuditOutcome

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
        sds.side_effect = [fake_sds_device_trace(), fake_sds_endpoint_trace()]
        client.return_value.__aenter__.return_value.post = AsyncMock(return_value=Response(200, content=payload))
        if audit_fails:

            async def fail_validation_audit(**kwargs):
                if kwargs.get("action") == "validate_fhir_bundle":
                    raise AuditFailureException("Audit unavailable")

            audit.side_effect = fail_validation_audit
            with pytest.raises(AuditFailureException):
                await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())
            telemetry.assert_called_once()
            convert.assert_not_called()
            cache.assert_not_called()
            return
        response = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())
        assert response.status_code == 502
        body = json.loads(response.body)
        assert body == {"success": False, "error": "FHIR bundle malformed"}
        telemetry.assert_called_once()
        event = audit.call_args.kwargs
        assert event["action"] == "validate_fhir_bundle"
        assert event["outcome"] == AuditOutcome.fail
        assert event["error_code"] == "502"
        assert event["nhs_number"] == "9690937278"
        assert event["detail"] == {"exception_type": type(telemetry.call_args.args[0]).__name__}
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
    from app.audit.models import AuditOutcome

    fake_pds = load_pds(9690937278)
    mock_lookup_patient.return_value = fake_pds
    mock_sds_trace.side_effect = [fake_sds_device_trace(), fake_sds_endpoint_trace()]

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
    from app.audit.models import AuditOutcome

    fake_pds = load_pds(9690937278)
    mock_lookup_patient.return_value = fake_pds
    mock_sds_trace.side_effect = [fake_sds_device_trace(), fake_sds_endpoint_trace()]

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
    from app.audit.models import AuditOutcome

    fake_pds = load_pds(9690937278)
    mock_lookup_patient.return_value = fake_pds
    mock_sds_trace.side_effect = [fake_sds_device_trace(), fake_sds_endpoint_trace()]

    synthetic_body = "SYNTHETIC_FHIR_CONTENT_MUST_NOT_BE_AUDITED"
    mock_client = AsyncMock()
    mock_client.post.return_value = Response(status_code=200, content=synthetic_body)
    mock_async_client.return_value.__aenter__.return_value = mock_client

    result = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())

    assert result.status_code == 502
    body = json.loads(result.body)
    assert body == {"success": False, "error": "FHIR bundle malformed"}

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
    from app.audit.audit import AuditFailureException
    from app.audit.models import AuditOutcome

    upstream_secret = "SECRET_UPSTREAM_BODY"

    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.gpconnect.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.gpconnect.httpx.AsyncClient") as client,
    ):
        sds.side_effect = [RuntimeError(f"Upstream HTTP 500: {upstream_secret}")]

        if audit_fails:

            async def fail_device_audit(**kwargs):
                if kwargs["action"] == "sds_trace" and kwargs["outcome"] == AuditOutcome.fail:
                    raise AuditFailureException("Audit unavailable")

            audit.side_effect = fail_device_audit
            with pytest.raises(AuditFailureException):
                await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())
        else:
            response = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())
            assert response.status_code == 502
            body = json.loads(response.body)
            assert body == {
                "success": False,
                "error": "SDS Device resolution failed for ODS code E82665",
            }
            assert upstream_secret not in response.body.decode()

        # Audit verification
        device_audits = [call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "sds_trace"]
        assert len(device_audits) == 1
        assert device_audits[0]["outcome"] == AuditOutcome.fail
        assert device_audits[0]["error_code"] == "502"
        assert device_audits[0]["detail"] == {
            "stage": "sds_device",
            "exception_type": "RuntimeError",
        }
        for arg in device_audits[0].values():
            assert upstream_secret not in str(arg)

        # Device failure prevents Endpoint lookup
        assert sds.call_count == 1
        assert not any(call.kwargs["action"] == "fhir_endpoint_trace" for call in audit.await_args_list)
        mock_self_issuer.assert_not_awaited()
        client.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("audit_fails", [False, True])
async def test_sds_endpoint_failure_does_not_expose_upstream_secret(mock_self_issuer, audit_fails):
    from app.audit.audit import AuditFailureException
    from app.audit.models import AuditOutcome

    upstream_secret = "SECRET_UPSTREAM_BODY"

    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.gpconnect.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.gpconnect.httpx.AsyncClient") as client,
    ):
        sds.side_effect = [fake_sds_device_trace(), RuntimeError(f"Upstream HTTP 500: {upstream_secret}")]

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
            body = json.loads(response.body)
            assert body == {
                "success": False,
                "error": "SDS Endpoint resolution failed for ODS code E82665",
            }
            assert upstream_secret not in response.body.decode()

        # Audit verification
        endpoint_audits = [
            call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "fhir_endpoint_trace"
        ]
        assert len(endpoint_audits) == 1
        assert endpoint_audits[0]["outcome"] == AuditOutcome.fail
        assert endpoint_audits[0]["error_code"] == "502"
        assert endpoint_audits[0]["detail"] == {
            "stage": "sds_endpoint",
            "exception_type": "RuntimeError",
        }
        for arg in endpoint_audits[0].values():
            assert upstream_secret not in str(arg)

        # Downstream request never attempted
        mock_self_issuer.assert_not_awaited()
        client.assert_not_called()
