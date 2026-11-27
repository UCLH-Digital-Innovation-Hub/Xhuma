"""Bounded, code-only classification of untrusted GP Connect failures."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class GPConnectFailure:
    http_status: int
    reason: str
    user_message: str
    provider_code: str | None = None


_MESSAGES = {
    "no_patient_consent": "GP record unavailable: patient has chosen not to share their GP record",
    "no_organisation_consent": "GP record unavailable: organisation has not consented to share the GP record",
    "access_denied": "GP record unavailable: access denied",
    "requesting_system_not_authorised": "GP record unavailable: requesting system is not authorised",
}
FAILURE_REASONS = frozenset((*_MESSAGES, "unknown_provider_error"))
_CODES = {
    "NO_PATIENT_CONSENT": "no_patient_consent",
    "NO_ORGANISATION_CONSENT": "no_organisation_consent",
    "ACCESS DENIED": "access_denied",
    "ACCESS_DENIED": "access_denied",
    "ASID_CHECK_FAILED": "requesting_system_not_authorised",
    "FOT_CHECK_FAILED": "requesting_system_not_authorised",
}
# NHS GP Connect test-harness legacy URI and STU3 Spine code system.
_CODE_SYSTEMS = frozenset(
    {
        "https://fhir.nhs.uk/ValueSet/gpconnect-error-or-warning-code-1",
        "https://fhir.nhs.uk/STU3/CodeSystem/Spine-ErrorOrWarningCode-1",
    }
)
_AUTH_CODE = re.compile(r"(ASID_CHECK_FAILED|FOT_CHECK_FAILED)(?:_[A-Z0-9]{1,16}){1,4}")
_SAFE_DISPLAYS = frozenset(
    {
        "Patient has not provided consent to share data",
        "Patient not found",
        "Internal server error",
        "Invalid parameter",
        "Reference not found",
        "Access denied",
    }
)
_MAX_ISSUES = 32
_MAX_CODINGS = 16
_MAX_CODE_LENGTH = 96
_MAX_DISPLAY_LENGTH = 64


def classify_operation_outcome(status: int, payload: object) -> GPConnectFailure:
    """Read only details.coding codes/displays; never read issue diagnostics.

    Unknown codes cannot establish a reason. Conflicting known reasons and
    truncated scans fail closed rather than making a partial consent claim.
    """
    reasons: set[str] = set()
    provider_codes: set[str] = set()
    displays: dict[str, None] = {}
    truncated = False
    issues = payload.get("issue") if isinstance(payload, dict) else None
    if not isinstance(payload, dict) or payload.get("resourceType") != "OperationOutcome":
        issues = None
    if isinstance(issues, list):
        truncated = len(issues) > _MAX_ISSUES
        for issue in issues[:_MAX_ISSUES]:
            if not isinstance(issue, dict) or issue.get("severity") not in ("error", "fatal"):
                continue
            details = issue.get("details")
            codings = details.get("coding") if isinstance(details, dict) else None
            if not isinstance(codings, list):
                continue
            truncated |= len(codings) > _MAX_CODINGS
            for coding in codings[:_MAX_CODINGS]:
                if not isinstance(coding, dict):
                    continue
                # Missing systems are tolerated for existing supplier compatibility;
                # explicitly foreign vocabularies cannot establish a GP Connect reason.
                system = coding.get("system")
                if system is not None and (not isinstance(system, str) or system not in _CODE_SYSTEMS):
                    continue
                code = coding.get("code")
                if isinstance(code, str) and len(code) <= _MAX_CODE_LENGTH:
                    canonical = code
                    match = _AUTH_CODE.fullmatch(code)
                    if match:
                        canonical = match.group(1)
                    reason = _CODES.get(canonical)
                    if reason:
                        reasons.add(reason)
                        provider_codes.add(canonical)
                display = coding.get("display")
                if isinstance(display, str) and len(display) <= _MAX_DISPLAY_LENGTH:
                    display = " ".join(display.split())
                    if display in _SAFE_DISPLAYS and (display != "Patient not found" or status == 404):
                        displays[display] = None

    if len(reasons) == 1 and not truncated:
        reason = next(iter(reasons))
        provider_code = next(iter(provider_codes)) if len(provider_codes) == 1 else None
        return GPConnectFailure(status, reason, _MESSAGES[reason], provider_code)

    message = f"Error from GP Connect endpoint {status}"
    if displays and len(reasons) <= 1 and not truncated:
        message += ": " + " | ".join(displays)
    return GPConnectFailure(status, "unknown_provider_error", message)
