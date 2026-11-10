"""Code-only failure classification and end-to-end privacy regressions."""

import importlib
import json
import xml.etree.ElementTree as ET
from unittest.mock import AsyncMock, patch

import pytest
from httpx import Response

from app.audit.models import AuditOutcome
from app.gpconnect import _fetch_gpconnect_record
from app.gpconnect_stages.failure import FAILURE_REASONS, classify_operation_outcome
from app.tests.configure_tests import load_pds
from app.tests.fixtures.saml_attributes import saml
from app.tests.gpconnect_helpers import fake_sds_device_trace, fake_sds_endpoint_trace, get_mock_request

PRIVATE = "PRIVATE_PROVIDER_DIAGNOSTIC NHS 9434765919 secret-provider-credential"
CASES = [
    (
        "NO_PATIENT_CONSENT",
        "no_patient_consent",
        "NO_PATIENT_CONSENT",
        "GP record unavailable: patient has chosen not to share their GP record",
    ),
    (
        "NO_ORGANISATION_CONSENT",
        "no_organisation_consent",
        "NO_ORGANISATION_CONSENT",
        "GP record unavailable: organisation has not consented to share the GP record",
    ),
    (
        "ASID_CHECK_FAILED",
        "requesting_system_not_authorised",
        "ASID_CHECK_FAILED",
        "GP record unavailable: requesting system is not authorised",
    ),
    (
        "ASID_CHECK_FAILED_MESSAGESENDER_100000000001",
        "requesting_system_not_authorised",
        "ASID_CHECK_FAILED",
        "GP record unavailable: requesting system is not authorised",
    ),
    (
        "FOT_CHECK_FAILED",
        "requesting_system_not_authorised",
        "FOT_CHECK_FAILED",
        "GP record unavailable: requesting system is not authorised",
    ),
    (
        "FOT_CHECK_FAILED_MESSAGESENDER_100000000001",
        "requesting_system_not_authorised",
        "FOT_CHECK_FAILED",
        "GP record unavailable: requesting system is not authorised",
    ),
    ("ACCESS DENIED", "access_denied", "ACCESS DENIED", "GP record unavailable: access denied"),
    ("ACCESS_DENIED", "access_denied", "ACCESS_DENIED", "GP record unavailable: access denied"),
    (PRIVATE, "unknown_provider_error", None, "Error from GP Connect endpoint 403"),
]


def outcome(*codes, diagnostics=PRIVATE):
    return {
        "resourceType": "OperationOutcome",
        "issue": [
            {
                "severity": "error",
                "code": "processing",
                "diagnostics": diagnostics,
                "details": {"text": PRIVATE, "coding": [{"code": code, "display": PRIVATE} for code in codes]},
            }
        ],
    }


@pytest.mark.parametrize("code,reason,canonical,message", CASES)
@pytest.mark.parametrize("wire", ["json", "soap"])
@pytest.mark.usefixtures("mock_self_issuer")
@pytest.mark.asyncio
async def test_failure_pipeline(code, reason, canonical, message, wire, tmp_path, caplog):
    iti38 = importlib.import_module("app.soap.responses.iti_38")
    with (
        patch("app.gpconnect.USE_RELAY", False),
        patch("app.gpconnect_stages.patient.lookup_patient", new_callable=AsyncMock, return_value=load_pds(9690937278)),
        patch(
            "app.gpconnect_stages.routing.sds_trace",
            new_callable=AsyncMock,
            side_effect=[fake_sds_endpoint_trace(), fake_sds_device_trace()],
        ),
        patch("app.gpconnect_stages.transport.create_nhs_ssl_context"),
        patch("app.gpconnect_stages.transport.httpx.AsyncClient") as factory,
        patch("app.audit.audit.attempt_audit", new_callable=AsyncMock) as audit,
        patch("app.logging.record_application_failure") as telemetry,
        patch("app.soap.responses.iti_38.redis_client", autospec=True) as cache,
        patch("app.soap.responses.iti_38.attempt_audit", new_callable=AsyncMock) as soap_audit,
        patch("app.gpconnect_stages.document.convert_bundle", new_callable=AsyncMock) as convert,
    ):
        payload = outcome(code)
        if code == "NO_PATIENT_CONSENT":
            payload["issue"][0]["details"]["coding"][0]["display"] = "Patient has not provided consent to share data"
        factory.return_value.__aenter__.return_value.post = AsyncMock(return_value=Response(403, json=payload))
        cache.get.return_value = None
        if wire == "json":
            response = await _fetch_gpconnect_record(
                9690937278, saml, log_dir=str(tmp_path), request=get_mock_request()
            )
            assert response.status_code == 403
            body = json.loads(response.body)
            expected = {"success": False, "error": message, "reason": reason, "http_status": 403}
            if canonical:
                expected["provider_code"] = canonical
            assert body == expected
            public = response.body.decode()
            assert not (tmp_path / "403_response.json").exists()
        else:
            public = await iti38.iti_38_response(get_mock_request(), 9690937278, "ceid", "queryid", saml)
            root = ET.fromstring(public)
            error = root.find(".//{urn:oasis:names:tc:ebxml-regrep:xsd:rs:3.0}RegistryError")
            assert error is not None
            assert error.attrib["errorCode"] == "XDSRegistryError"
            assert error.attrib["codeContext"] == message
            assert (
                root.find(".//{urn:oasis:names:tc:ebxml-regrep:xsd:query:3.0}AdhocQueryResponse")
                .attrib["status"]
                .endswith(":Failure")
            )
            assert "patient not found" not in public.lower()
            assert soap_audit.await_args.kwargs["error_code"] == "GP_CONNECT_FAILURE"
            assert soap_audit.await_args.kwargs["detail"]["reason"] == reason
        convert.assert_not_awaited()

    gp_audit = audit.await_args.kwargs
    assert gp_audit["action"] == "gpconnect_request"
    assert gp_audit["outcome"] == AuditOutcome.fail
    assert gp_audit["error_code"] == "403"
    detail = {"step": "gpconnect_request.response_status", "transport": "direct", "reason": reason}
    if canonical:
        detail["provider_code"] = canonical
    assert gp_audit["detail"] == detail
    telemetry.assert_called_once()
    diagnostic = str(telemetry.call_args.args[0])
    assert diagnostic == (
        f"GP Connect failure (status: 403, reason: {reason}, provider_code: {canonical}, "
        "step: gpconnect_request.response_status)"
    )
    # Request debug files legitimately contain the requested patient, but no
    # provider response fields may enter any debug file or failure surface.
    debug = "".join(path.read_text() for path in tmp_path.rglob("*") if path.is_file())
    assert "9690937278" not in caplog.text + diagnostic
    surfaces = public + caplog.text + diagnostic + debug
    surfaces += repr([call.kwargs.get("detail") for call in audit.await_args_list])
    surfaces += repr([call.kwargs.get("detail") for call in soap_audit.await_args_list])
    for secret in (PRIVATE, "9434765919", "secret-provider-credential", "PRIVATE_PROVIDER_DIAGNOSTIC"):
        assert secret not in surfaces
    if canonical and code != canonical:
        assert code not in surfaces


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        "not JSON",
        42,
        {},
        {"resourceType": "Bundle", "issue": outcome("NO_PATIENT_CONSENT")["issue"]},
        {"resourceType": "OperationOutcome", "issue": {}},
        {
            "resourceType": "OperationOutcome",
            "issue": [
                None,
                [],
                "bad",
                {"details": None},
                {"details": {"coding": {}}},
                {"details": {"coding": [None, [], {"code": []}, {"display": {}}]}},
            ],
        },
    ],
)
def test_malformed_payload_is_unknown(payload):
    failure = classify_operation_outcome(403, payload)
    assert failure.reason == "unknown_provider_error"
    assert failure.provider_code is None
    assert failure.user_message == "Error from GP Connect endpoint 403"


@pytest.mark.parametrize("status", [400, 401, 403, 404, 500, 503])
def test_status_or_diagnostic_alone_never_establishes_consent(status):
    payload = outcome("UNKNOWN", diagnostics="NO_PATIENT_CONSENT")
    payload["issue"][0]["code"] = "NO_PATIENT_CONSENT"
    failure = classify_operation_outcome(status, payload)
    assert failure.http_status == status
    assert failure.reason == "unknown_provider_error"
    assert failure.provider_code is None


@pytest.mark.parametrize(
    "diagnostics", [PRIVATE, "GP Connect is disabled", "Patient sharing disabled; NO_PATIENT_CONSENT"]
)
def test_access_denied_diagnostics_do_not_infer_consent_or_disabled_service(diagnostics):
    failure = classify_operation_outcome(403, outcome("ACCESS DENIED", diagnostics=diagnostics))
    assert failure.reason == "access_denied"
    assert failure.user_message == "GP record unavailable: access denied"


def test_multiple_issues_and_codings_deduplicate_known_codes():
    payload = outcome("NO_PATIENT_CONSENT", "NO_PATIENT_CONSENT", PRIVATE)
    payload["issue"].extend(outcome("NO_PATIENT_CONSENT")["issue"])
    failure = classify_operation_outcome(403, payload)
    assert failure.reason == "no_patient_consent"
    assert failure.provider_code == "NO_PATIENT_CONSENT"


@pytest.mark.parametrize("separate_issues", [False, True])
def test_conflicting_reasons_fail_closed(separate_issues):
    payload = outcome("NO_PATIENT_CONSENT", "ACCESS DENIED")
    if separate_issues:
        payload = outcome("NO_PATIENT_CONSENT")
        payload["issue"].extend(outcome("ACCESS DENIED")["issue"])
    failure = classify_operation_outcome(403, payload)
    assert failure.reason == "unknown_provider_error"
    assert failure.provider_code is None


def test_same_reason_with_distinct_canonical_codes_has_no_arbitrary_provider_code():
    failure = classify_operation_outcome(403, outcome("ASID_CHECK_FAILED", "FOT_CHECK_FAILED"))
    assert failure.reason == "requesting_system_not_authorised"
    assert failure.provider_code is None


@pytest.mark.parametrize("field,limit", [("issues", 32), ("codings", 16)])
@pytest.mark.parametrize("extra", [0, 1])
def test_scan_bounds_fail_closed_instead_of_partial_consent(field, limit, extra):
    payload = outcome("NO_PATIENT_CONSENT")
    if field == "issues":
        payload["issue"] *= limit + extra
    else:
        payload["issue"][0]["details"]["coding"] *= limit + extra
    failure = classify_operation_outcome(403, payload)
    assert failure.reason == ("unknown_provider_error" if extra else "no_patient_consent")
    assert failure.provider_code == (None if extra else "NO_PATIENT_CONSENT")


@pytest.mark.parametrize(
    "code",
    [
        "NO_PATIENT_CONSENT ",
        "no_patient_consent",
        "X" * 97,
        "ASID_CHECK_FAILED_" + "A" * 17,
        "FOT_CHECK_FAILED_A_B_C_D_E",
        "ASID_CHECK_FAILED_private",
    ],
)
def test_unrecognised_or_unbounded_codes_are_not_echoed(code):
    failure = classify_operation_outcome(403, outcome(code))
    assert failure.reason == "unknown_provider_error"
    assert failure.provider_code is None
    assert code not in failure.user_message


@pytest.mark.parametrize(
    "displays",
    [
        [" Access\ndenied\r\n"],
        [" Access\ndenied\r\n", "Access denied"],
    ],
)
def test_safe_displays_are_normalized_deduplicated_and_bounded(displays):
    payload = outcome("UNKNOWN")
    payload["issue"][0]["details"]["coding"] = [
        {"display": value} for value in displays + [PRIVATE, "Patient not found", " " * 65 + "Access denied"]
    ]
    failure = classify_operation_outcome(403, payload)
    assert failure.user_message == "Error from GP Connect endpoint 403: Access denied"
    assert failure.reason in FAILURE_REASONS


@pytest.mark.parametrize("display", ["Access denied " + PRIVATE, " " * 65 + "Access denied", "X" * 10000])
def test_unsafe_or_oversized_display_is_suppressed(display):
    payload = outcome("UNKNOWN")
    payload["issue"][0]["details"]["coding"] = [{"display": display}]
    failure = classify_operation_outcome(403, payload)
    assert failure.user_message == "Error from GP Connect endpoint 403"


@pytest.mark.parametrize("family", ["ASID_CHECK_FAILED", "FOT_CHECK_FAILED"])
def test_authorisation_suffix_bounds_and_deduplication(family):
    code = family + ("_" + "A" * 16) * 4
    failure = classify_operation_outcome(403, outcome(family, code))
    assert failure.reason == "requesting_system_not_authorised"
    assert failure.provider_code == family
    assert code not in failure.user_message


@pytest.mark.parametrize("system", ["https://example.invalid/unrelated", "", [], {}])
def test_foreign_or_malformed_code_system_cannot_establish_consent(system):
    payload = outcome("NO_PATIENT_CONSENT")
    payload["issue"][0]["details"]["coding"][0]["system"] = system
    assert classify_operation_outcome(403, payload).reason == "unknown_provider_error"


@pytest.mark.parametrize(
    "system",
    [
        "https://fhir.nhs.uk/ValueSet/gpconnect-error-or-warning-code-1",
        "https://fhir.nhs.uk/STU3/CodeSystem/Spine-ErrorOrWarningCode-1",
    ],
)
def test_recognised_code_system_establishes_consent(system):
    payload = outcome("NO_PATIENT_CONSENT")
    payload["issue"][0]["details"]["coding"][0]["system"] = system
    assert classify_operation_outcome(403, payload).reason == "no_patient_consent"


@pytest.mark.parametrize("severity", ["information", "warning", None, [], {}])
def test_non_error_issue_cannot_override_actual_failure(severity):
    payload = outcome("NO_PATIENT_CONSENT")
    payload["issue"][0]["severity"] = severity
    payload["issue"].extend(outcome("INTERNAL_SERVER_ERROR")["issue"])
    failure = classify_operation_outcome(500, payload)
    assert failure.reason == "unknown_provider_error"
    assert failure.user_message == "Error from GP Connect endpoint 500"


def test_conflicting_or_truncated_outcomes_do_not_forward_consent_display():
    payload = outcome("NO_PATIENT_CONSENT", "ACCESS DENIED")
    payload["issue"][0]["details"]["coding"][0]["display"] = "Patient has not provided consent to share data"
    assert classify_operation_outcome(403, payload).user_message == "Error from GP Connect endpoint 403"
    payload["issue"] *= 33
    assert classify_operation_outcome(403, payload).user_message == "Error from GP Connect endpoint 403"
