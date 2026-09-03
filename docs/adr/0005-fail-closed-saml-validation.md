# ADR 0005: Fail-Closed SAML Validation

## Status
Accepted (Implemented: August 2026)

## Context
Xhuma acts as a bridge between Epic Care Everywhere and NHS GP Connect. Epic transmits clinical requests as IHE SOAP messages, which include SAML assertions detailing the identity and role of the requesting clinician. Prior to this decision, Xhuma passively extracted SAML attributes for downstream auditing but did not cryptographically reject requests if the SAML Issuer was unauthorized, relying heavily on mTLS for boundary protection.

## Decision
We implemented strict, "fail-closed" SAML Issuer validation across all inbound ITI endpoints (`ITI-38`, `ITI-39`, `ITI-47`, `ITI-55`).
1. **Trusted Issuers**: We introduced a `SAML_TRUSTED_ISSUER` environment variable (allowing a pipe-separated list of authorized certificate CNs).
2. **Cryptographic Rejection**: If an inbound SOAP request lacks a valid SAML assertion, or if the `saml2:Issuer` does not match the trusted allowlist, the middleware immediately throws a `401 Unauthorized` / `403 Forbidden` error.
3. **Opaque Errors**: The application safely degrades and rejects incomplete SAML payloads without leaking stack traces.

## Consequences

### Positive
- **Defense in Depth**: Prevents unauthorized access even if the outer mTLS boundary is somehow bypassed or misconfigured.
- **Clinical Governance**: Cryptographically guarantees that every transaction processed by Xhuma is tied to a verified, authorized clinician identity before querying the NHS Spine.
- **Audit Integrity**: Ensures that downstream logging and OpenTelemetry tracing contain complete and accurate clinician identities, fulfilling SCAL requirements.

### Negative
- **Operational Friction**: Introducing new downstream consumers or changing Epic CA certificates requires manual updates to the `SAML_TRUSTED_ISSUER` environment variable.
