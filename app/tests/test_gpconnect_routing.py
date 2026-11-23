import json
import logging
from unittest.mock import AsyncMock, patch

import pytest

from app.audit.audit import AuditFailureException
from app.audit.models import AuditOutcome
from app.gpconnect import _fetch_gpconnect_record, gpconnect
from app.tests.configure_tests import load_pds
from app.tests.fixtures.saml_attributes import saml
from app.tests.gpconnect_helpers import fake_sds_endpoint_trace, get_mock_request

pytest_plugins = ("pytest_asyncio",)
pytestmark = pytest.mark.usefixtures("mock_self_issuer")


@pytest.mark.asyncio
@patch("app.audit.audit.attempt_audit", new_callable=AsyncMock)
@patch("app.gpconnect_stages.routing.sds_trace", new_callable=AsyncMock)
@patch("app.gpconnect_stages.patient.lookup_patient", new_callable=AsyncMock)
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
@patch("app.gpconnect_stages.routing.sds_trace", new_callable=AsyncMock)
@patch("app.gpconnect_stages.patient.lookup_patient", new_callable=AsyncMock)
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
        patch("app.gpconnect_stages.patient.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect_stages.routing.sds_trace", new_callable=AsyncMock) as sds,
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
        patch("app.gpconnect_stages.patient.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect_stages.routing.sds_trace", new_callable=AsyncMock) as sds,
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
        patch("app.gpconnect_stages.patient.lookup_patient", new_callable=AsyncMock, return_value=load_pds(nhs_no)),
        patch("app.gpconnect_stages.routing.sds_trace", new_callable=AsyncMock) as sds,
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
        patch("app.gpconnect_stages.patient.lookup_patient", new_callable=AsyncMock, return_value=load_pds(nhs_no)),
        patch("app.gpconnect_stages.routing.sds_trace", new_callable=AsyncMock) as sds,
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
        patch("app.gpconnect_stages.patient.lookup_patient", new_callable=AsyncMock, return_value=load_pds(nhs_no)),
        patch("app.gpconnect_stages.routing.sds_trace", new_callable=AsyncMock) as sds,
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
        patch("app.gpconnect_stages.patient.lookup_patient", new_callable=AsyncMock, return_value=load_pds(nhs_no)),
        patch("app.gpconnect_stages.routing.sds_trace", new_callable=AsyncMock) as sds,
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
        patch("app.gpconnect_stages.patient.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect_stages.routing.sds_trace", new_callable=AsyncMock) as sds,
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
@pytest.mark.parametrize("audit_fails", [False, True])
async def test_sds_device_failure_does_not_expose_upstream_secret(mock_self_issuer, audit_fails):

    upstream_secret = "SECRET_UPSTREAM_BODY"
    upstream_error = RuntimeError(f"Upstream HTTP 500: {upstream_secret}")

    with (
        patch("app.gpconnect_stages.patient.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect_stages.routing.sds_trace", new_callable=AsyncMock) as sds,
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
        patch("app.gpconnect_stages.patient.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect_stages.routing.sds_trace", new_callable=AsyncMock) as sds,
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
        patch("app.gpconnect_stages.patient.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect_stages.routing.sds_trace", new_callable=AsyncMock) as sds,
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
        patch("app.gpconnect_stages.patient.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect_stages.routing.sds_trace", new_callable=AsyncMock) as sds,
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
        patch("app.gpconnect_stages.patient.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect_stages.routing.sds_trace", new_callable=AsyncMock) as sds,
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
        patch("app.gpconnect_stages.patient.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch("app.gpconnect_stages.routing.sds_trace", new_callable=AsyncMock) as sds,
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
