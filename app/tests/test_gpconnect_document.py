import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from httpx import Response

from app.audit.audit import AuditFailureException
from app.audit.models import AuditOutcome
from app.gpconnect import gpconnect
from app.tests.configure_tests import load_pds
from app.tests.fixtures.saml_attributes import saml
from app.tests.gpconnect_fixtures import observed_pipeline as observed_pipeline
from app.tests.gpconnect_helpers import fake_sds_device_trace, fake_sds_endpoint_trace, get_mock_request

pytest_plugins = ("pytest_asyncio",)
pytestmark = pytest.mark.usefixtures("mock_self_issuer")


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
        patch("app.gpconnect_stages.routing.sds_trace", new_callable=AsyncMock) as sds,
        patch("app.gpconnect_stages.patient.lookup_patient", new_callable=AsyncMock) as pds,
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
@patch("app.logging.record_application_failure")
@patch("app.gpconnect_stages.transport.httpx.AsyncClient")
@patch("app.gpconnect_stages.transport.create_nhs_ssl_context")
@patch("app.gpconnect_stages.routing.sds_trace", new_callable=AsyncMock)
@patch("app.gpconnect_stages.patient.lookup_patient", new_callable=AsyncMock)
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
async def test_document_conversion_http_exception(observed_pipeline):
    p = observed_pipeline
    p.failures["convert"] = HTTPException(409, "synthetic conversion detail")
    response = await p.gp.gpconnect(p.nhs, p.saml, request=p.request)
    assert response.status_code == 409
    assert json.loads(response.body)["error"] == "Failed to convert FHIR Bundle to CCDA: synthetic conversion detail"
    assert p.audits[-1]["action"] == "gpconnect_request"
    assert p.audits[-1]["outcome"] == AuditOutcome.ok
    assert not p.published
