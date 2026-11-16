import json
from unittest.mock import AsyncMock, patch

import pytest

from app.audit.audit import AuditFailureException
from app.audit.models import AuditOutcome
from app.gpconnect import gpconnect
from app.tests.configure_tests import load_pds
from app.tests.fixtures.saml_attributes import saml
from app.tests.gpconnect_fixtures import observed_pipeline as observed_pipeline
from app.tests.gpconnect_helpers import get_mock_request

pytest_plugins = ("pytest_asyncio",)
pytestmark = pytest.mark.usefixtures("mock_self_issuer")


@pytest.mark.asyncio
@patch("app.audit.audit.attempt_audit", new_callable=AsyncMock)
@patch("app.gpconnect_stages.patient.lookup_patient", new_callable=AsyncMock)
async def test_gpconnect_returns_400_for_invalid_nhs_number(mock_lookup_patient, mock_attempt_audit):
    result = await gpconnect(1234567890, saml_attrs=saml, request=get_mock_request())
    body = json.loads(result.body)

    assert result.status_code == 400
    assert body["success"] is False
    assert "not a valid NHS number" in body["error"]

    mock_lookup_patient.assert_not_called()


@pytest.mark.asyncio
@patch("app.audit.audit.attempt_audit", new_callable=AsyncMock)
@patch("app.gpconnect_stages.patient.lookup_patient", new_callable=AsyncMock)
async def test_gpconnect_returns_502_when_pds_lookup_fails(mock_lookup_patient, mock_attempt_audit):
    mock_lookup_patient.side_effect = Exception("PDS unavailable")

    result = await gpconnect(9690937278, saml_attrs=saml, request=get_mock_request())
    body = json.loads(result.body)

    assert result.status_code == 502
    assert body["success"] is False
    assert "PDS lookup failed" in body["error"]


@pytest.mark.asyncio
@patch("app.audit.audit.attempt_audit", new_callable=AsyncMock)
@patch("app.gpconnect_stages.patient.lookup_patient", new_callable=AsyncMock)
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
        patch("app.gpconnect_stages.patient.lookup_patient", new_callable=AsyncMock, return_value=fake_pds),
        patch("app.gpconnect_stages.routing.sds_trace", new_callable=AsyncMock) as sds,
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
async def test_patient_first_security_code_controls_denial(observed_pipeline):
    p = observed_pipeline
    p.patient["meta"]["security"] = [{"code": "R"}, {"code": "U"}]
    response = await p.gp.gpconnect(p.nhs, p.saml, request=p.request)
    assert response.status_code == 403
    assert p.events == ["nhs.validate", "pds", "audit:pds_lookup:ok", "audit:check_restriction:deny", "json.response"]
