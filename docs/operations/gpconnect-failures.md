# GP Connect record-unavailability failures

A non-200 GP Connect response remains a failure. HTTP status is retained independently
of the controlled failure reason. Neither consent enforcement nor routing, cache,
PDS lookup, document generation, or ITI-39 behaviour changes.

## Classification boundary

Only `OperationOutcome.issue[]` entries with severity `error` or `fatal` contribute.
`issue.details.coding[].code` is matched case-sensitively, without fuzzy matching:

| Code | Xhuma reason |
| --- | --- |
| `NO_PATIENT_CONSENT` | `no_patient_consent` |
| `NO_ORGANISATION_CONSENT` | `no_organisation_consent` |
| `ACCESS DENIED`, `ACCESS_DENIED` | `access_denied` |
| `ASID_CHECK_FAILED`, `FOT_CHECK_FAILED` | `requesting_system_not_authorised` |
| Other, malformed, conflicting or truncated outcomes | `unknown_provider_error` |

ASID/FOT family codes additionally accept 1–4 underscore-separated uppercase ASCII
alphanumeric suffix segments of 1–16 characters each. Only the canonical family is
retained, never its supplier/system identifier suffix. These families distinguish
request authorisation failures, not patient sharing choices.

Explicit coding systems must be one of:

- `https://fhir.nhs.uk/ValueSet/gpconnect-error-or-warning-code-1`
- `https://fhir.nhs.uk/STU3/CodeSystem/Spine-ErrorOrWarningCode-1`

Missing systems are tolerated for supplier compatibility; explicitly unrelated or
malformed systems are ignored. The legacy URI is evidenced by the
[mirrored NHS test-harness consent outcome](https://github.com/uk-gov-mirror/nhsconnect.TKW_GP_CONNECT_configs/blob/HEAD/simulator_config/operation_outcomes/operation_outcome_npc.xml),
and the STU3 URI by its
[Spine code system](https://github.com/uk-gov-mirror/nhsconnect.TKW_GP_CONNECT_configs/blob/HEAD/validator_config/fhir_assets/STU3-FHIR-Assets/CodeSystems/CodeSystem-Spine-ErrorOrWarningCode-1.xml).
These historical mirrors are supporting evidence, not a claim of current supplier
conformance. HTTP 403 alone never establishes patient consent denial.

Scanning is limited to 32 issues, 16 codings per issue and 96 characters per code.
Duplicate reasons collapse; conflicting recognised reasons or exceeded scan limits
produce the generic reason and message. Multiple codes for the same reason retain
that reason but omit `provider_code` unless there is one canonical value.

## Privacy and compatibility

Known reasons use fixed Xhuma messages. Unknown codes are not retained. The generic
fallback preserves only these exact display phrases after whitespace normalisation
(input at most 64 characters), deduplicated in provider order:

- `Patient has not provided consent to share data`
- `Patient not found` (only for HTTP 404)
- `Internal server error`
- `Invalid parameter`
- `Reference not found`
- `Access denied`

Displays never establish a structured reason. Arbitrary displays, `details.text`,
and all `diagnostics` are ignored. Conflicting/truncated outcomes do not forward
displays. This intentionally removes arbitrary supplier prose and identifier-bearing
ASID display strings previously returned and logged.

No raw non-200 response debug file is written, including when `log_dir` is supplied.
Failure logs/telemetry contain only status, controlled reason, canonical code and
step. Audit retains the HTTP status as `error_code`, adding bounded `detail.reason`
and, where unambiguous, canonical `detail.provider_code`. ITI-38 audit retains
`GP_CONNECT_FAILURE`, adding allowlisted `detail.reason`. No audit schema migration
is needed. Existing governed patient audit fields and request/success debug behaviour
are unchanged; this is not a whole-application privacy certification.

The JSON failure response adds `reason`, `http_status` and optional `provider_code`
to the existing `success` and `error` fields. Consumers must tolerate additive fields.

## Capability disabled at practice: deliberately not inferred

The NHS England [Structured API catalogue](https://digital.nhs.uk/developer/api-catalogue/gp-connect-access-record-structured-fhir)
links the archived specification. During this investigation the exact authoritative
capability-disabled diagnostic wording could not be verified from that archive or
repository fixtures. Therefore **no diagnostic patterns are treated as authoritative**
and `capability_disabled_at_practice` is not implemented. `ACCESS DENIED` with any
diagnostic, including disabled-looking prose, remains `access_denied`.

A future extension needs verified wording/version and narrow whole-string
normalised matching, with negative/privacy tests. No substring matching and no
inference from HTTP 403, access denial alone, or missing SDS routes is appropriate.
Organisation consent configuration, patient choice, and requesting-system
authorisation are already distinguished where the coding evidence supports them.

## ITI-38 and the Epic boundary

Genuine PDS patient-not-found remains `XDSUnknownPatientId`. GP Connect consent and
access failures remain `XDSRegistryError`, with failure status and useful
`RegistryError.codeContext`. There is no new XDS code, SOAP Fault, empty success, or
synthetic clinical document.

Example for the Epic/Care Everywhere team (inside the existing ITI-38 error list):

```xml
<rs:RegistryError
    xmlns:rs="urn:oasis:names:tc:ebxml-regrep:xsd:rs:3.0"
    errorCode="XDSRegistryError"
    codeContext="GP record unavailable: patient has chosen not to share their GP record"
    severity="urn:oasis:names:tc:ebxml-regrep:ErrorSeverityType:Error" />
```

**Can Epic surface `RegistryError.codeContext`, or configure its user-facing mapping
for this condition?** Xhuma makes the wire response truthful; this does not guarantee
Epic displays that wording. Epic may still use a generic mapping for `XDSRegistryError`.
