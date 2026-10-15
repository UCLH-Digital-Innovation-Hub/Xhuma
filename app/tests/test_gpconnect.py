import json
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from httpx import Response

from app.gpconnect import (
    PDS_GP_ODS_INVALID,
    SDS_ASID_MISSING,
    SDS_DEVICE_EMPTY_BUNDLE,
    SDS_DEVICE_INVALID_RESPONSE,
    SDS_DEVICE_LOOKUP_FAILED,
    SDS_ENDPOINT_AMBIGUOUS,
    SDS_ENDPOINT_EMPTY_BUNDLE,
    SDS_ENDPOINT_INVALID_RESPONSE,
    SDS_ENDPOINT_LOOKUP_FAILED,
    SDS_IDENTITY_AMBIGUOUS,
    SDS_IDENTITY_INVALID,
    SDS_PARTYKEY_MISSING,
    gpconnect,
)
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
                    "status": "active",
                    "address": "https://fake-gpconnect-endpoint",
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
    assert claims["aud"] == "https://fake-gpconnect-endpoint"
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
    assert body["error"] == "SDS Device resolution failed"
    assert "SDS unavailable" not in body["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "device_trace,expected_reason",
    [
        pytest.param(None, SDS_DEVICE_INVALID_RESPONSE, id="null-response"),
        pytest.param({}, SDS_DEVICE_INVALID_RESPONSE, id="missing-entry"),
        pytest.param({"resourceType": "Bundle", "entry": None}, SDS_DEVICE_INVALID_RESPONSE, id="null-entry"),
        pytest.param({"resourceType": "Bundle", "entry": []}, SDS_DEVICE_EMPTY_BUNDLE, id="empty-entry"),
        pytest.param({"entry": []}, SDS_DEVICE_INVALID_RESPONSE, id="missing-bundle-resourcetype"),
        pytest.param({"resourceType": "Bundle", "entry": [None]}, SDS_DEVICE_INVALID_RESPONSE, id="null-first-entry"),
        pytest.param({"resourceType": "Bundle", "entry": [{}]}, SDS_DEVICE_INVALID_RESPONSE, id="missing-resource"),
        pytest.param(
            {"resourceType": "Bundle", "entry": [{"resource": None}]}, SDS_DEVICE_INVALID_RESPONSE, id="null-resource"
        ),
        pytest.param(
            {"resourceType": "Bundle", "entry": [{"resource": {"resourceType": "Patient"}}]},
            SDS_DEVICE_INVALID_RESPONSE,
            id="non-device-resource",
        ),
        pytest.param(
            {
                "resourceType": "Bundle",
                "entry": [
                    {
                        "resource": {
                            "identifier": [
                                {"system": "https://fhir.nhs.uk/Id/nhsSpineASID", "value": "200000000000"},
                                {"system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey", "value": "FAKE-PARTY-KEY"},
                            ]
                        }
                    }
                ],
            },
            SDS_DEVICE_INVALID_RESPONSE,
            id="missing-device-resourcetype",
        ),
    ],
)
async def test_invalid_device_bundle_fails_sds_trace_without_proceeding(
    mock_self_issuer, device_trace, expected_reason
):
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
        assert body["error"] == "SDS Device resolution failed"
        assert "list index out of range" not in body["error"]
        assert "IndexError" not in body["error"]

        device_audits = [call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "sds_trace"]
        assert len(device_audits) == 1
        assert device_audits[0]["outcome"] == AuditOutcome.fail
        assert device_audits[0]["error_code"] == "502"
        assert device_audits[0]["detail"]["stage"] == "sds_device"
        assert device_audits[0]["detail"]["reason"] == expected_reason

        # Prove no sds_trace success audit was emitted
        assert not any(
            call.kwargs["action"] == "sds_trace" and call.kwargs["outcome"] == AuditOutcome.ok
            for call in audit.await_args_list
        )

        # Prove Stage 2 (resolve_asid_partykey) was never attempted
        assert not any(call.kwargs["action"] == "resolve_asid_partykey" for call in audit.await_args_list)

        # Prove Stage 3 (Endpoint lookup) was never attempted (sds called only once)
        assert sds.call_count == 1
        assert not any(call.kwargs["action"] == "fhir_endpoint_trace" for call in audit.await_args_list)

        mock_self_issuer.assert_not_awaited()
        client.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "device_trace,expected_reason",
    [
        pytest.param(
            {"resourceType": "Bundle", "entry": [{"resource": {"resourceType": "Device"}}]},
            SDS_IDENTITY_INVALID,
            id="missing-identifier",
        ),
        pytest.param(
            {"resourceType": "Bundle", "entry": [{"resource": {"resourceType": "Device", "identifier": None}}]},
            SDS_IDENTITY_INVALID,
            id="null-identifier",
        ),
        pytest.param(
            {"resourceType": "Bundle", "entry": [{"resource": {"resourceType": "Device", "identifier": []}}]},
            SDS_IDENTITY_INVALID,
            id="empty-identifier",
        ),
        pytest.param(
            {
                "resourceType": "Bundle",
                "entry": [
                    {
                        "resource": {
                            "resourceType": "Device",
                            "identifier": [
                                {
                                    "system": "https://fhir.nhs.uk/Id/nhsSpineASID",
                                    "value": "200000000000",
                                }
                            ],
                        }
                    }
                ],
            },
            SDS_PARTYKEY_MISSING,
            id="missing-mhspartykey",
        ),
        pytest.param(
            {
                "resourceType": "Bundle",
                "entry": [
                    {
                        "resource": {
                            "resourceType": "Device",
                            "identifier": [
                                {
                                    "system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey",
                                    "value": "FAKE-PARTY-KEY",
                                }
                            ],
                        }
                    }
                ],
            },
            SDS_ASID_MISSING,
            id="missing-asid",
        ),
        pytest.param(
            {
                "resourceType": "Bundle",
                "entry": [
                    {
                        "resource": {
                            "resourceType": "Device",
                            "identifier": [
                                {
                                    "system": "https://fhir.nhs.uk/Id/nhsSpineASID",
                                    "value": "",
                                },
                                {
                                    "system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey",
                                    "value": "FAKE-PARTY-KEY",
                                },
                            ],
                        }
                    }
                ],
            },
            SDS_ASID_MISSING,
            id="empty-asid",
        ),
        pytest.param(
            {
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
                                    "value": " \t\n",
                                },
                            ],
                        }
                    }
                ],
            },
            SDS_PARTYKEY_MISSING,
            id="blank-mhspartykey",
        ),
        pytest.param(
            {
                "resourceType": "Bundle",
                "entry": [
                    {
                        "resource": {
                            "resourceType": "Device",
                            "identifier": [
                                {
                                    "system": "https://fhir.nhs.uk/Id/nhsSpineASID",
                                    "value": 12345,
                                },
                                {
                                    "system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey",
                                    "value": "FAKE-PARTY-KEY",
                                },
                            ],
                        }
                    }
                ],
            },
            SDS_IDENTITY_INVALID,
            id="nonstring-asid",
        ),
        pytest.param(
            {
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
                                    "system": "https://fhir.nhs.uk/Id/nhsSpineASID",
                                    "value": "200000000001",
                                },
                                {
                                    "system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey",
                                    "value": "FAKE-PARTY-KEY",
                                },
                            ],
                        }
                    }
                ],
            },
            SDS_IDENTITY_AMBIGUOUS,
            id="conflicting-asids-same-device",
        ),
    ],
)
async def test_invalid_device_identity_fails_resolve_asid_partykey_without_proceeding(
    mock_self_issuer, device_trace, expected_reason
):
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
        assert body["error"] == "Unable to resolve SDS Device identity"

        # Stage 1: sds_trace succeeded semantically
        device_audits = [call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "sds_trace"]
        assert len(device_audits) == 1
        assert device_audits[0]["outcome"] == AuditOutcome.ok

        # Stage 2: resolve_asid_partykey failed
        identity_audits = [
            call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "resolve_asid_partykey"
        ]
        assert len(identity_audits) == 1
        assert identity_audits[0]["outcome"] == AuditOutcome.fail
        assert identity_audits[0]["error_code"] == "502"
        assert identity_audits[0]["detail"]["stage"] == "resolve_asid_partykey"
        assert identity_audits[0]["detail"]["reason"] == expected_reason

        # Prove no resolve_asid_partykey success audit was emitted
        assert not any(
            call.kwargs["action"] == "resolve_asid_partykey" and call.kwargs["outcome"] == AuditOutcome.ok
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
            assert json.loads(response.body)["error"] == "SDS Device resolution failed"
            assert "SDS unavailable" not in json.loads(response.body)["error"]

        device_audits = [call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "sds_trace"]
        assert len(device_audits) == 1
        assert device_audits[0]["outcome"] == AuditOutcome.fail
        assert device_audits[0]["error_code"] == "502"
        assert device_audits[0]["detail"] == {
            "stage": "sds_device",
            "reason": SDS_DEVICE_LOOKUP_FAILED,
            "exception_type": "RuntimeError",
        }
        assert sds.call_count == 1
        assert not any(call.kwargs["action"] == "resolve_asid_partykey" for call in audit.await_args_list)
        assert not any(call.kwargs["action"] == "fhir_endpoint_trace" for call in audit.await_args_list)
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

        # Exactly one resolve_asid_partykey success audit
        identity_audits = [
            call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "resolve_asid_partykey"
        ]
        assert len(identity_audits) == 1
        assert identity_audits[0]["outcome"] == AuditOutcome.ok

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
        pytest.param({"entry": []}, id="missing-bundle-resourcetype"),
        pytest.param({"resourceType": "Bundle", "entry": []}, id="empty-bundle"),
        pytest.param({"resourceType": "Bundle", "entry": [None]}, id="null-first-entry"),
        pytest.param({"resourceType": "Bundle", "entry": [{}]}, id="missing-resource"),
        pytest.param({"resourceType": "Bundle", "entry": [{"resource": None}]}, id="null-resource"),
        pytest.param(
            {"resourceType": "Bundle", "entry": [{"resource": {"status": "active", "address": "https://fake"}}]},
            id="missing-endpoint-resourcetype",
        ),
        pytest.param(
            {
                "resourceType": "Bundle",
                "entry": [
                    {
                        "resource": {
                            "resourceType": "Endpoint",
                            "status": "suspended",
                            "address": "https://fake",
                            "identifier": [
                                {"system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey", "value": "FAKE-PARTY-KEY"}
                            ],
                        }
                    }
                ],
            },
            id="inactive-endpoint",
        ),
        pytest.param(
            {
                "resourceType": "Bundle",
                "entry": [
                    {
                        "resource": {
                            "resourceType": "Endpoint",
                            "status": "active",
                            "address": "https://fake",
                        }
                    }
                ],
            },
            id="absent-partykey",
        ),
        pytest.param(
            {
                "resourceType": "Bundle",
                "entry": [
                    {
                        "resource": {
                            "resourceType": "Endpoint",
                            "status": "active",
                            "address": "https://fake",
                            "identifier": [{"system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey", "value": "WRONG-KEY"}],
                        }
                    }
                ],
            },
            id="wrong-partykey",
        ),
        pytest.param(
            {
                "resourceType": "Bundle",
                "entry": [
                    {
                        "resource": {
                            "resourceType": "Endpoint",
                            "status": "active",
                            "address": "test-url",
                            "identifier": [
                                {"system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey", "value": "FAKE-PARTY-KEY"}
                            ],
                        }
                    }
                ],
            },
            id="test-url-endpoint",
        ),
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
        assert body["error"] == "SDS Endpoint resolution failed"

        # Earlier stages succeeded
        assert any(
            call.kwargs["action"] == "sds_trace" and call.kwargs["outcome"] == AuditOutcome.ok
            for call in audit.await_args_list
        )
        assert any(
            call.kwargs["action"] == "resolve_asid_partykey" and call.kwargs["outcome"] == AuditOutcome.ok
            for call in audit.await_args_list
        )

        endpoint_audits = [
            call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "fhir_endpoint_trace"
        ]
        assert len(endpoint_audits) == 1
        assert endpoint_audits[0]["outcome"] == AuditOutcome.fail
        assert endpoint_audits[0]["error_code"] == "502"
        assert endpoint_audits[0]["detail"]["stage"] == "sds_endpoint"
        assert endpoint_audits[0]["detail"]["reason"] in [
            SDS_ENDPOINT_EMPTY_BUNDLE,
            SDS_ENDPOINT_INVALID_RESPONSE,
        ]
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
            assert json.loads(response.body)["error"] == "SDS Endpoint resolution failed"
            assert "SDS unavailable" not in json.loads(response.body)["error"]

        # Earlier stages succeeded
        assert any(
            call.kwargs["action"] == "sds_trace" and call.kwargs["outcome"] == AuditOutcome.ok
            for call in audit.await_args_list
        )
        assert any(
            call.kwargs["action"] == "resolve_asid_partykey" and call.kwargs["outcome"] == AuditOutcome.ok
            for call in audit.await_args_list
        )

        endpoint_audits = [
            call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "fhir_endpoint_trace"
        ]
        assert len(endpoint_audits) == 1
        assert endpoint_audits[0]["outcome"] == AuditOutcome.fail
        assert endpoint_audits[0]["error_code"] == "502"
        assert endpoint_audits[0]["detail"] == {
            "stage": "sds_endpoint",
            "reason": SDS_ENDPOINT_LOOKUP_FAILED,
            "exception_type": "RuntimeError",
        }
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

    assert mock_attempt_audit.call_count == 5
    actions = [call.kwargs["action"] for call in mock_attempt_audit.await_args_list]
    assert actions == [
        "sds_trace",
        "resolve_asid_partykey",
        "fhir_endpoint_trace",
        "sds_self_lookup",
        "gpconnect_request",
    ]
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

            async def fail_device_audit(**kwargs):
                if kwargs["action"] == "sds_trace" and kwargs["outcome"] == AuditOutcome.fail:
                    raise AuditFailureException("Audit unavailable")

            audit.side_effect = fail_device_audit
            with pytest.raises(AuditFailureException):
                await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())

            mock_record_failure.assert_not_called()
        else:
            response = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())
            assert response.status_code == 502
            body = json.loads(response.body)
            assert body == {
                "success": False,
                "error": "SDS Device resolution failed",
            }
            assert upstream_secret not in response.body.decode()

            mock_record_failure.assert_called_once()
            recorded_exception = mock_record_failure.call_args[0][0]
            assert isinstance(recorded_exception, RuntimeError)
            assert upstream_secret not in str(recorded_exception)
            assert upstream_secret not in repr(recorded_exception)
            assert str(recorded_exception) == f"SDS Device resolution failed [{SDS_DEVICE_LOOKUP_FAILED}]"
            assert recorded_exception is not upstream_error

        # Audit verification
        device_audits = [call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "sds_trace"]
        assert len(device_audits) == 1
        assert device_audits[0]["outcome"] == AuditOutcome.fail
        assert device_audits[0]["error_code"] == "502"
        assert device_audits[0]["detail"] == {
            "stage": "sds_device",
            "reason": SDS_DEVICE_LOOKUP_FAILED,
            "exception_type": "RuntimeError",
        }
        for arg in device_audits[0].values():
            assert upstream_secret not in str(arg)

        # Device failure prevents Identity resolution and Endpoint lookup
        assert sds.call_count == 1
        assert not any(call.kwargs["action"] == "resolve_asid_partykey" for call in audit.await_args_list)
        assert not any(call.kwargs["action"] == "fhir_endpoint_trace" for call in audit.await_args_list)
        mock_self_issuer.assert_not_awaited()
        client.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("audit_fails", [False, True])
async def test_resolve_asid_partykey_failure_does_not_expose_upstream_secret(mock_self_issuer, audit_fails):
    from app.audit.audit import AuditFailureException
    from app.audit.models import AuditOutcome

    upstream_secret = "SECRET_UPSTREAM_BODY"
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
        sds.side_effect = [malformed_device]

        if audit_fails:

            async def fail_identity_audit(**kwargs):
                if kwargs["action"] == "resolve_asid_partykey" and kwargs["outcome"] == AuditOutcome.fail:
                    raise AuditFailureException("Audit unavailable")

            audit.side_effect = fail_identity_audit
            with pytest.raises(AuditFailureException):
                await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())

            mock_record_failure.assert_not_called()
        else:
            response = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())
            assert response.status_code == 502
            body = json.loads(response.body)
            assert body == {
                "success": False,
                "error": "Unable to resolve SDS Device identity",
            }
            assert upstream_secret not in response.body.decode()

            mock_record_failure.assert_called_once()
            recorded_exception = mock_record_failure.call_args[0][0]
            assert isinstance(recorded_exception, RuntimeError)
            assert upstream_secret not in str(recorded_exception)
            assert upstream_secret not in repr(recorded_exception)
            assert str(recorded_exception) == f"Unable to resolve SDS Device identity [{SDS_ASID_MISSING}]"

        # Stage 1 succeeded
        device_audits = [call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "sds_trace"]
        assert len(device_audits) == 1
        assert device_audits[0]["outcome"] == AuditOutcome.ok

        # Stage 2 failed
        identity_audits = [
            call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "resolve_asid_partykey"
        ]
        assert len(identity_audits) == 1
        assert identity_audits[0]["outcome"] == AuditOutcome.fail
        assert identity_audits[0]["error_code"] == "502"
        assert identity_audits[0]["detail"] == {
            "stage": "resolve_asid_partykey",
            "reason": SDS_ASID_MISSING,
        }
        for arg in identity_audits[0].values():
            assert upstream_secret not in str(arg)

        # Stage 3 never attempted
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
    upstream_error = RuntimeError(f"Upstream HTTP 500: {upstream_secret}")

    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.gpconnect.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.gpconnect.record_application_failure") as mock_record_failure,
        patch("app.gpconnect.httpx.AsyncClient") as client,
    ):
        sds.side_effect = [fake_sds_device_trace(), upstream_error]

        if audit_fails:

            async def fail_endpoint_audit(**kwargs):
                if kwargs["action"] == "fhir_endpoint_trace" and kwargs["outcome"] == AuditOutcome.fail:
                    raise AuditFailureException("Audit unavailable")

            audit.side_effect = fail_endpoint_audit
            with pytest.raises(AuditFailureException):
                await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())

            mock_record_failure.assert_not_called()
        else:
            response = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())
            assert response.status_code == 502
            body = json.loads(response.body)
            assert body == {
                "success": False,
                "error": "SDS Endpoint resolution failed",
            }
            assert upstream_secret not in response.body.decode()

            mock_record_failure.assert_called_once()
            recorded_exception = mock_record_failure.call_args[0][0]
            assert isinstance(recorded_exception, RuntimeError)
            assert upstream_secret not in str(recorded_exception)
            assert upstream_secret not in repr(recorded_exception)
            assert str(recorded_exception) == f"SDS Endpoint resolution failed [{SDS_ENDPOINT_LOOKUP_FAILED}]"
            assert recorded_exception is not upstream_error

        # Preceding stages succeeded
        device_audits = [call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "sds_trace"]
        assert len(device_audits) == 1
        assert device_audits[0]["outcome"] == AuditOutcome.ok
        identity_audits = [
            call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "resolve_asid_partykey"
        ]
        assert len(identity_audits) == 1
        assert identity_audits[0]["outcome"] == AuditOutcome.ok

        # Audit verification
        endpoint_audits = [
            call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "fhir_endpoint_trace"
        ]
        assert len(endpoint_audits) == 1
        assert endpoint_audits[0]["outcome"] == AuditOutcome.fail
        assert endpoint_audits[0]["error_code"] == "502"
        assert endpoint_audits[0]["detail"] == {
            "stage": "sds_endpoint",
            "reason": SDS_ENDPOINT_LOOKUP_FAILED,
            "exception_type": "RuntimeError",
        }
        for arg in endpoint_audits[0].values():
            assert upstream_secret not in str(arg)

        # Downstream request never attempted
        mock_self_issuer.assert_not_awaited()
        client.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("audit_fails", [False, True])
async def test_resolve_gp_ods_failure_does_not_expose_upstream_secret(mock_self_issuer, audit_fails):
    from app.audit.audit import AuditFailureException
    from app.audit.models import AuditOutcome

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

            mock_record_failure.assert_not_called()
        else:
            response = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())
            assert response.status_code == 500
            body = json.loads(response.body)
            assert body == {
                "success": False,
                "error": "Unable to read GP ODS from PDS response",
            }
            assert upstream_secret not in response.body.decode()

            mock_record_failure.assert_called_once()
            recorded_exception = mock_record_failure.call_args[0][0]
            assert isinstance(recorded_exception, RuntimeError)
            assert upstream_secret not in str(recorded_exception)
            assert upstream_secret not in repr(recorded_exception)
            assert str(recorded_exception) == f"Unable to read GP ODS from PDS response [{PDS_GP_ODS_INVALID}]"
            assert recorded_exception is not upstream_error

        # Audit verification
        resolve_audits = [call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "resolve_gp_ods"]
        assert len(resolve_audits) == 1
        assert resolve_audits[0]["outcome"] == AuditOutcome.fail
        assert resolve_audits[0]["error_code"] == "502"
        assert resolve_audits[0]["detail"] == {
            "stage": "resolve_gp_ods",
            "reason": PDS_GP_ODS_INVALID,
            "exception_type": "KeyError",
        }
        for arg in resolve_audits[0].values():
            assert upstream_secret not in str(arg)

        # Downstream SDS never attempted
        sds.assert_not_awaited()
        mock_self_issuer.assert_not_awaited()
        client.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "trace_type,sds_side_effect,expected_stage,expected_reason,expected_status,expected_error_substr",
    [
        (
            "device",
            [{"resourceType": "Bundle", "entry": []}],
            "sds_device",
            SDS_DEVICE_EMPTY_BUNDLE,
            502,
            "SDS Device resolution failed",
        ),
        (
            "device",
            [{"entry": None}],
            "sds_device",
            SDS_DEVICE_INVALID_RESPONSE,
            502,
            "SDS Device resolution failed",
        ),
        (
            "device",
            [RuntimeError("Upstream timeout")],
            "sds_device",
            SDS_DEVICE_LOOKUP_FAILED,
            502,
            "SDS Device resolution failed",
        ),
        (
            "identity",
            [
                {
                    "resourceType": "Bundle",
                    "entry": [
                        {
                            "resource": {
                                "resourceType": "Device",
                                "identifier": [{"system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey", "value": "KEY1"}],
                            }
                        }
                    ],
                }
            ],
            "resolve_asid_partykey",
            SDS_ASID_MISSING,
            502,
            "Unable to resolve SDS Device identity",
        ),
        (
            "identity",
            [
                {
                    "resourceType": "Bundle",
                    "entry": [
                        {
                            "resource": {
                                "resourceType": "Device",
                                "identifier": [{"system": "https://fhir.nhs.uk/Id/nhsSpineASID", "value": "123"}],
                            }
                        }
                    ],
                }
            ],
            "resolve_asid_partykey",
            SDS_PARTYKEY_MISSING,
            502,
            "Unable to resolve SDS Device identity",
        ),
        (
            "identity",
            [
                {
                    "resourceType": "Bundle",
                    "entry": [{"resource": {"resourceType": "Device"}}],
                }
            ],
            "resolve_asid_partykey",
            SDS_IDENTITY_INVALID,
            502,
            "Unable to resolve SDS Device identity",
        ),
        (
            "identity",
            [
                {
                    "resourceType": "Bundle",
                    "entry": [
                        {
                            "resource": {
                                "resourceType": "Device",
                                "identifier": [
                                    {"system": "https://fhir.nhs.uk/Id/nhsSpineASID", "value": "ASID1"},
                                    {"system": "https://fhir.nhs.uk/Id/nhsSpineASID", "value": "ASID2"},
                                    {"system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey", "value": "KEY1"},
                                ],
                            }
                        }
                    ],
                }
            ],
            "resolve_asid_partykey",
            SDS_IDENTITY_AMBIGUOUS,
            502,
            "Unable to resolve SDS Device identity",
        ),
        (
            "endpoint",
            [fake_sds_device_trace(), {"resourceType": "Bundle", "entry": []}],
            "sds_endpoint",
            SDS_ENDPOINT_EMPTY_BUNDLE,
            502,
            "SDS Endpoint resolution failed",
        ),
        (
            "endpoint",
            [fake_sds_device_trace(), {"entry": [{"resource": {}}]}],
            "sds_endpoint",
            SDS_ENDPOINT_INVALID_RESPONSE,
            502,
            "SDS Endpoint resolution failed",
        ),
        (
            "endpoint",
            [fake_sds_device_trace(), {"entry": [{}]}],
            "sds_endpoint",
            SDS_ENDPOINT_INVALID_RESPONSE,
            502,
            "SDS Endpoint resolution failed",
        ),
        (
            "endpoint",
            [fake_sds_device_trace(), RuntimeError("Endpoint timeout")],
            "sds_endpoint",
            SDS_ENDPOINT_LOOKUP_FAILED,
            502,
            "SDS Endpoint resolution failed",
        ),
        (
            "endpoint",
            [
                fake_sds_device_trace(),
                {
                    "resourceType": "Bundle",
                    "entry": [
                        {
                            "resource": {
                                "resourceType": "Endpoint",
                                "status": "active",
                                "address": "https://endpoint1.nhs.uk",
                                "identifier": [
                                    {"system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey", "value": "FAKE-PARTY-KEY"}
                                ],
                            }
                        },
                        {
                            "resource": {
                                "resourceType": "Endpoint",
                                "status": "active",
                                "address": "https://endpoint2.nhs.uk",
                                "identifier": [
                                    {"system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey", "value": "FAKE-PARTY-KEY"}
                                ],
                            }
                        },
                    ],
                },
            ],
            "sds_endpoint",
            SDS_ENDPOINT_AMBIGUOUS,
            502,
            "SDS Endpoint resolution failed",
        ),
    ],
)
async def test_safe_reason_codes_emitted(
    mock_self_issuer,
    trace_type,
    sds_side_effect,
    expected_stage,
    expected_reason,
    expected_status,
    expected_error_substr,
):
    from app.audit.models import AuditOutcome

    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.gpconnect.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.gpconnect.record_application_failure") as mock_record_failure,
        patch("app.gpconnect.httpx.AsyncClient") as client,
    ):
        sds.side_effect = sds_side_effect

        response = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())
        assert response.status_code == expected_status
        body = json.loads(response.body)
        assert body["success"] is False
        assert body["error"] == expected_error_substr

        action_map = {
            "sds_device": "sds_trace",
            "resolve_asid_partykey": "resolve_asid_partykey",
            "sds_endpoint": "fhir_endpoint_trace",
        }
        action = action_map[expected_stage]
        matching_audits = [call.kwargs for call in audit.await_args_list if call.kwargs["action"] == action]
        assert len(matching_audits) == 1
        assert matching_audits[0]["outcome"] == AuditOutcome.fail
        assert matching_audits[0]["detail"]["stage"] == expected_stage
        assert matching_audits[0]["detail"]["reason"] == expected_reason

        mock_record_failure.assert_called_once()
        recorded_err = mock_record_failure.call_args[0][0]
        assert isinstance(recorded_err, RuntimeError)
        assert expected_reason in str(recorded_err)
        client.assert_not_called()

        if expected_stage == "sds_device":
            assert not any(call.kwargs["action"] == "resolve_asid_partykey" for call in audit.await_args_list)
            assert not any(call.kwargs["action"] == "fhir_endpoint_trace" for call in audit.await_args_list)
            assert sds.call_count == 1
        elif expected_stage == "resolve_asid_partykey":
            sds_ok = [call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "sds_trace"]
            assert len(sds_ok) == 1 and sds_ok[0]["outcome"] == AuditOutcome.ok
            assert not any(call.kwargs["action"] == "fhir_endpoint_trace" for call in audit.await_args_list)
            assert sds.call_count == 1
        elif expected_stage == "sds_endpoint":
            sds_ok = [call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "sds_trace"]
            assert len(sds_ok) == 1 and sds_ok[0]["outcome"] == AuditOutcome.ok
            ident_ok = [
                call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "resolve_asid_partykey"
            ]
            assert len(ident_ok) == 1 and ident_ok[0]["outcome"] == AuditOutcome.ok
            assert sds.call_count == 2


@pytest.mark.asyncio
async def test_pds_gp_ods_invalid_reason_code_emitted(mock_self_issuer):
    from app.audit.models import AuditOutcome

    malformed_pds = {"meta": {"security": [{"code": "U"}]}, "generalPractitioner": []}

    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=malformed_pds),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.gpconnect.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.gpconnect.record_application_failure") as mock_record_failure,
        patch("app.gpconnect.httpx.AsyncClient") as client,
    ):
        response = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())
        assert response.status_code == 500
        body = json.loads(response.body)
        assert body["success"] is False
        assert body["error"] == "Unable to read GP ODS from PDS response"

        resolve_audits = [call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "resolve_gp_ods"]
        assert len(resolve_audits) == 1
        assert resolve_audits[0]["outcome"] == AuditOutcome.fail
        assert resolve_audits[0]["detail"]["stage"] == "resolve_gp_ods"
        assert resolve_audits[0]["detail"]["reason"] == PDS_GP_ODS_INVALID
        assert resolve_audits[0]["detail"]["exception_type"] == "IndexError"

        mock_record_failure.assert_called_once()
        recorded_err = mock_record_failure.call_args[0][0]
        assert isinstance(recorded_err, RuntimeError)
        assert PDS_GP_ODS_INVALID in str(recorded_err)
        sds.assert_not_awaited()
        client.assert_not_called()


def test_literal_reason_code_values():
    """Assert literal string values of the reason taxonomy rather than importing constants."""
    assert PDS_GP_ODS_INVALID == "PDS_GP_ODS_INVALID"
    assert SDS_DEVICE_LOOKUP_FAILED == "SDS_DEVICE_LOOKUP_FAILED"
    assert SDS_DEVICE_EMPTY_BUNDLE == "SDS_DEVICE_EMPTY_BUNDLE"
    assert SDS_DEVICE_INVALID_RESPONSE == "SDS_DEVICE_INVALID_RESPONSE"
    assert SDS_IDENTITY_INVALID == "SDS_IDENTITY_INVALID"
    assert SDS_IDENTITY_AMBIGUOUS == "SDS_IDENTITY_AMBIGUOUS"
    assert SDS_ASID_MISSING == "SDS_ASID_MISSING"
    assert SDS_PARTYKEY_MISSING == "SDS_PARTYKEY_MISSING"
    assert SDS_ENDPOINT_LOOKUP_FAILED == "SDS_ENDPOINT_LOOKUP_FAILED"
    assert SDS_ENDPOINT_EMPTY_BUNDLE == "SDS_ENDPOINT_EMPTY_BUNDLE"
    assert SDS_ENDPOINT_INVALID_RESPONSE == "SDS_ENDPOINT_INVALID_RESPONSE"
    assert SDS_ENDPOINT_AMBIGUOUS == "SDS_ENDPOINT_AMBIGUOUS"


@pytest.mark.asyncio
async def test_two_conflicting_device_identities_fails_closed(mock_self_issuer):
    """Two distinct Device identities in a bundle fail closed with SDS_IDENTITY_AMBIGUOUS."""
    from app.audit.models import AuditOutcome

    conflicting_devices = {
        "resourceType": "Bundle",
        "entry": [
            {
                "resource": {
                    "resourceType": "Device",
                    "identifier": [
                        {"system": "https://fhir.nhs.uk/Id/nhsSpineASID", "value": "200000000001"},
                        {"system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey", "value": "PARTY-A"},
                    ],
                }
            },
            {
                "resource": {
                    "resourceType": "Device",
                    "identifier": [
                        {"system": "https://fhir.nhs.uk/Id/nhsSpineASID", "value": "200000000002"},
                        {"system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey", "value": "PARTY-B"},
                    ],
                }
            },
        ],
    }

    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.gpconnect.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.gpconnect.httpx.AsyncClient") as client,
    ):
        sds.side_effect = [conflicting_devices, fake_sds_endpoint_trace()]

        response = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())
        assert response.status_code == 502
        body = json.loads(response.body)
        assert body["error"] == "Unable to resolve SDS Device identity"

        device_audits = [call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "sds_trace"]
        assert len(device_audits) == 1 and device_audits[0]["outcome"] == AuditOutcome.ok

        identity_audits = [
            call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "resolve_asid_partykey"
        ]
        assert len(identity_audits) == 1
        assert identity_audits[0]["outcome"] == AuditOutcome.fail
        assert identity_audits[0]["detail"]["reason"] == "SDS_IDENTITY_AMBIGUOUS"

        # Endpoint trace must never be called
        assert sds.call_count == 1
        assert not any(call.kwargs["action"] == "fhir_endpoint_trace" for call in audit.await_args_list)
        client.assert_not_called()


@pytest.mark.asyncio
async def test_duplicate_device_identities_deduplicate_safely(mock_self_issuer):
    """Duplicate identical Device identities deduplicate safely without ambiguity error."""
    from app.audit.models import AuditOutcome

    duplicate_devices = {
        "resourceType": "Bundle",
        "entry": [
            {
                "resource": {
                    "resourceType": "Device",
                    "identifier": [
                        {"system": "https://fhir.nhs.uk/Id/nhsSpineASID", "value": "200000000000"},
                        {"system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey", "value": "FAKE-PARTY-KEY"},
                    ],
                }
            },
            {
                "resource": {
                    "resourceType": "Device",
                    "identifier": [
                        {"system": "https://fhir.nhs.uk/Id/nhsSpineASID", "value": "200000000000"},
                        {"system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey", "value": "FAKE-PARTY-KEY"},
                    ],
                }
            },
        ],
    }

    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.gpconnect.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.gpconnect.create_nhs_ssl_context"),
        patch("app.gpconnect.httpx.AsyncClient") as client,
        patch("app.gpconnect.convert_bundle", new_callable=AsyncMock) as mock_convert,
        patch("app.gpconnect.base64_xml") as mock_b64,
        patch("app.gpconnect.redis_client.pipeline") as mock_pipeline,
    ):
        sds.side_effect = [duplicate_devices, fake_sds_endpoint_trace()]
        mock_response = MagicMock(status_code=200, text='{"resourceType": "Bundle", "type": "searchset", "entry": []}')
        mock_client_instance = AsyncMock()
        mock_client_instance.post.return_value = mock_response
        client.return_value.__aenter__.return_value = mock_client_instance
        mock_convert.return_value = {"ClinicalDocument": {}}
        mock_b64.return_value = "b64"
        mock_pipe = mock_pipeline.return_value
        mock_pipe.__aenter__.return_value = mock_pipe
        mock_pipe.execute = AsyncMock()

        response = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())
        assert response.status_code == 200
        assert json.loads(response.body)["success"] is True

        ident_audits = [
            call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "resolve_asid_partykey"
        ]
        assert len(ident_audits) == 1 and ident_audits[0]["outcome"] == AuditOutcome.ok


@pytest.mark.asyncio
async def test_two_distinct_endpoint_addresses_fails_closed(mock_self_issuer):
    """Two distinct active Endpoint addresses fail closed with SDS_ENDPOINT_AMBIGUOUS."""
    from app.audit.models import AuditOutcome

    conflicting_endpoints = {
        "resourceType": "Bundle",
        "entry": [
            {
                "resource": {
                    "resourceType": "Endpoint",
                    "status": "active",
                    "address": "https://endpoint-a.nhs.uk/fhir",
                    "identifier": [{"system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey", "value": "FAKE-PARTY-KEY"}],
                }
            },
            {
                "resource": {
                    "resourceType": "Endpoint",
                    "status": "active",
                    "address": "https://endpoint-b.nhs.uk/fhir",
                    "identifier": [{"system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey", "value": "FAKE-PARTY-KEY"}],
                }
            },
        ],
    }

    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.gpconnect.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.gpconnect.httpx.AsyncClient") as client,
    ):
        sds.side_effect = [fake_sds_device_trace(), conflicting_endpoints]

        response = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())
        assert response.status_code == 502
        body = json.loads(response.body)
        assert body["error"] == "SDS Endpoint resolution failed"

        endpoint_audits = [
            call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "fhir_endpoint_trace"
        ]
        assert len(endpoint_audits) == 1
        assert endpoint_audits[0]["outcome"] == AuditOutcome.fail
        assert endpoint_audits[0]["detail"]["reason"] == "SDS_ENDPOINT_AMBIGUOUS"
        client.assert_not_called()


@pytest.mark.asyncio
async def test_duplicate_endpoint_addresses_deduplicate_safely(mock_self_issuer):
    """Duplicate identical Endpoint addresses deduplicate safely and succeed."""
    from app.audit.models import AuditOutcome

    duplicate_endpoints = {
        "resourceType": "Bundle",
        "entry": [
            {
                "resource": {
                    "resourceType": "Endpoint",
                    "status": "active",
                    "address": "https://fake-gpconnect-endpoint",
                    "identifier": [{"system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey", "value": "FAKE-PARTY-KEY"}],
                }
            },
            {
                "resource": {
                    "resourceType": "Endpoint",
                    "status": "active",
                    "address": "https://fake-gpconnect-endpoint",
                    "identifier": [{"system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey", "value": "FAKE-PARTY-KEY"}],
                }
            },
        ],
    }

    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.gpconnect.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.gpconnect.create_nhs_ssl_context"),
        patch("app.gpconnect.httpx.AsyncClient") as client,
        patch("app.gpconnect.convert_bundle", new_callable=AsyncMock) as mock_convert,
        patch("app.gpconnect.base64_xml") as mock_b64,
        patch("app.gpconnect.redis_client.pipeline") as mock_pipeline,
    ):
        sds.side_effect = [fake_sds_device_trace(), duplicate_endpoints]
        mock_response = MagicMock(status_code=200, text='{"resourceType": "Bundle", "type": "searchset", "entry": []}')
        mock_client_instance = AsyncMock()
        mock_client_instance.post.return_value = mock_response
        client.return_value.__aenter__.return_value = mock_client_instance
        mock_convert.return_value = {"ClinicalDocument": {}}
        mock_b64.return_value = "b64"
        mock_pipe = mock_pipeline.return_value
        mock_pipe.__aenter__.return_value = mock_pipe
        mock_pipe.execute = AsyncMock()

        response = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())
        assert response.status_code == 200
        assert json.loads(response.body)["success"] is True

        endpoint_audits = [
            call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "fhir_endpoint_trace"
        ]
        assert len(endpoint_audits) == 1 and endpoint_audits[0]["outcome"] == AuditOutcome.ok


@pytest.mark.asyncio
async def test_inactive_endpoint_fails_closed(mock_self_issuer):
    """An Endpoint with status other than active fails closed."""
    from app.audit.models import AuditOutcome

    inactive_endpoints = {
        "resourceType": "Bundle",
        "entry": [
            {
                "resource": {
                    "resourceType": "Endpoint",
                    "status": "suspended",
                    "address": "https://fake-gpconnect-endpoint",
                    "identifier": [{"system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey", "value": "FAKE-PARTY-KEY"}],
                }
            }
        ],
    }

    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.gpconnect.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.gpconnect.httpx.AsyncClient") as client,
    ):
        sds.side_effect = [fake_sds_device_trace(), inactive_endpoints]

        response = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())
        assert response.status_code == 502
        body = json.loads(response.body)
        assert body["error"] == "SDS Endpoint resolution failed"

        endpoint_audits = [
            call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "fhir_endpoint_trace"
        ]
        assert len(endpoint_audits) == 1
        assert endpoint_audits[0]["outcome"] == AuditOutcome.fail
        assert endpoint_audits[0]["detail"]["reason"] == "SDS_ENDPOINT_INVALID_RESPONSE"
        client.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_addr", ["/relative/endpoint", "ftp://endpoint.nhs.uk", "invalid-url", "", "test-url"])
async def test_malformed_relative_endpoint_url_fails_closed(mock_self_issuer, invalid_addr):
    """Non-HTTP(S) or relative Endpoint addresses fail closed."""
    from app.audit.models import AuditOutcome

    invalid_endpoints = {
        "resourceType": "Bundle",
        "entry": [
            {
                "resource": {
                    "resourceType": "Endpoint",
                    "status": "active",
                    "address": invalid_addr,
                    "identifier": [{"system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey", "value": "FAKE-PARTY-KEY"}],
                }
            }
        ],
    }

    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.gpconnect.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.gpconnect.httpx.AsyncClient") as client,
    ):
        sds.side_effect = [fake_sds_device_trace(), invalid_endpoints]

        response = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())
        assert response.status_code == 502
        body = json.loads(response.body)
        assert body["error"] == "SDS Endpoint resolution failed"

        endpoint_audits = [
            call.kwargs for call in audit.await_args_list if call.kwargs["action"] == "fhir_endpoint_trace"
        ]
        assert len(endpoint_audits) == 1
        assert endpoint_audits[0]["outcome"] == AuditOutcome.fail
        assert endpoint_audits[0]["detail"]["reason"] == "SDS_ENDPOINT_INVALID_RESPONSE"
        client.assert_not_called()


@pytest.mark.asyncio
async def test_no_entry_zero_assumption_device_selection(mock_self_issuer):
    """Valid Device resource appearing at entry[1] after a non-Device entry is correctly selected."""
    non_device_first_bundle = {
        "resourceType": "Bundle",
        "entry": [
            {"resource": {"resourceType": "Organization", "name": "Some GP Practice"}},
            {
                "resource": {
                    "resourceType": "Device",
                    "identifier": [
                        {"system": "https://fhir.nhs.uk/Id/nhsSpineASID", "value": "200000000000"},
                        {"system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey", "value": "FAKE-PARTY-KEY"},
                    ],
                }
            },
        ],
    }

    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.gpconnect.attempt_audit", new_callable=AsyncMock),
        patch("app.gpconnect.create_nhs_ssl_context"),
        patch("app.gpconnect.httpx.AsyncClient") as client,
        patch("app.gpconnect.convert_bundle", new_callable=AsyncMock) as mock_convert,
        patch("app.gpconnect.base64_xml") as mock_b64,
        patch("app.gpconnect.redis_client.pipeline") as mock_pipeline,
    ):
        sds.side_effect = [non_device_first_bundle, fake_sds_endpoint_trace()]
        mock_response = MagicMock(status_code=200, text='{"resourceType": "Bundle", "type": "searchset", "entry": []}')
        mock_client_instance = AsyncMock()
        mock_client_instance.post.return_value = mock_response
        client.return_value.__aenter__.return_value = mock_client_instance
        mock_convert.return_value = {"ClinicalDocument": {}}
        mock_b64.return_value = "b64"
        mock_pipe = mock_pipeline.return_value
        mock_pipe.__aenter__.return_value = mock_pipe
        mock_pipe.execute = AsyncMock()

        response = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())
        assert response.status_code == 200
        assert json.loads(response.body)["success"] is True


@pytest.mark.asyncio
async def test_no_entry_zero_assumption_endpoint_selection(mock_self_issuer):
    """Valid Endpoint resource appearing at entry[1] after a non-Endpoint entry is correctly selected."""
    non_endpoint_first_bundle = {
        "resourceType": "Bundle",
        "entry": [
            {"resource": {"resourceType": "Device", "id": "other-device"}},
            {
                "resource": {
                    "resourceType": "Endpoint",
                    "status": "active",
                    "address": "https://fake-gpconnect-endpoint",
                    "identifier": [{"system": "https://fhir.nhs.uk/Id/nhsMhsPartyKey", "value": "FAKE-PARTY-KEY"}],
                }
            },
        ],
    }

    with (
        patch("app.gpconnect.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.gpconnect.attempt_audit", new_callable=AsyncMock),
        patch("app.gpconnect.create_nhs_ssl_context"),
        patch("app.gpconnect.httpx.AsyncClient") as client,
        patch("app.gpconnect.convert_bundle", new_callable=AsyncMock) as mock_convert,
        patch("app.gpconnect.base64_xml") as mock_b64,
        patch("app.gpconnect.redis_client.pipeline") as mock_pipeline,
    ):
        sds.side_effect = [fake_sds_device_trace(), non_endpoint_first_bundle]
        mock_response = MagicMock(status_code=200, text='{"resourceType": "Bundle", "type": "searchset", "entry": []}')
        mock_client_instance = AsyncMock()
        mock_client_instance.post.return_value = mock_response
        client.return_value.__aenter__.return_value = mock_client_instance
        mock_convert.return_value = {"ClinicalDocument": {}}
        mock_b64.return_value = "b64"
        mock_pipe = mock_pipeline.return_value
        mock_pipe.__aenter__.return_value = mock_pipe
        mock_pipe.execute = AsyncMock()

        response = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())
        assert response.status_code == 200
        assert json.loads(response.body)["success"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure_stage,sds_mock",
    [
        ("resolve_gp_ods", None),
        ("sds_device", [RuntimeError("upstream device failure")]),
        (
            "resolve_asid_partykey",
            [
                {
                    "resourceType": "Bundle",
                    "entry": [
                        {
                            "resource": {
                                "resourceType": "Device",
                                "identifier": [
                                    {"system": "https://fhir.nhs.uk/Id/nhsSpineASID", "value": "200000000000"}
                                ],
                            }
                        }
                    ],
                }
            ],
        ),
        ("sds_endpoint", [fake_sds_device_trace(), RuntimeError("upstream endpoint failure")]),
    ],
)
async def test_raw_nhs_number_absent_from_application_logging(mock_self_issuer, failure_stage, sds_mock, caplog):
    """Raw NHS numbers must never appear in application logs during failure paths."""
    import logging

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
