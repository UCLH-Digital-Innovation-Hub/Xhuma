# INT Matrix Cutover Assurance Record (2026-10-06)

## A. Objective
- Brownfield migration of existing INT from legacy deployment path to matrix model.
- Preserve state, resources, and data.
- Ensure no destructive teardown occurs.

## B. Pre-cutover State
- Existing backend/state was preserved.
- Required state anchors were successfully verified.
- Existing INT workload was initially running on the prior SHA-tagged image.
- Epic, PDS, and SDS configuration settings were retained.

## C. Initial Matrix Migration
- Reviewed immutable plan confirmed exact plan binding/hash safeguards.
- The Apply result and shared infrastructure guard result indicated expected execution.
- `ENV` was corrected from `prod` to `int`.
- `ORG_ASID` was corrected from a blank string to the intended configured value.
- `CCDA_EXPIRY_HOURS` was introduced.

## D. Runtime Registry Incident
- After the first infrastructure reconciliation, the old image failed to cold-start.
- The App Service reported an `ImagePullUnauthorizedFailure`.
- Root cause: The persistent runtime registry username was derived from `github.actor`, while `CR_PAT` was a separately configured credential. 
- No manual Azure patch was used to bypass or hotfix the issue.
- A source fix was introduced to use `vars.GHCR_USERNAME` to establish a stable runtime username paired with `CR_PAT`.
- The existing old image subsequently cold-started successfully, proving the root-cause remediation before a new candidate deployment.

## E. Final Run #44
- Execution occurred in Matrix Deployment Pilot run #44.
- Exact plan hash: `8db7fc5546a9c321ae5102977dafa7dd3a8a2d923b6917667f1667dfbeff745e`.
- The plan indicated 0 resources added, 1 changed (only `azurerm_linux_web_app.app`), and 0 destroyed.
- The shared infrastructure plan confirmed no changes (0/0/0).
- The exact immutable image digest (`sha256:88d67cbdaa8829fd0823c3268cc14a10af77ecf0cbfada8971492f44dd3db5e5`) was successfully deployed.
- The image pull was successful.
- Application startup was successful (`/health` returned HTTP 200).
- Epic CA verification successfully loaded 5 certificates.

## F. Functional Acceptance
**Proven:**
- Epic to Xhuma TLS connectivity is established.
- Epic client certificate and mTLS acceptance functions correctly.
- SAML security context correctly reaches Xhuma.
- ITI-55 processing succeeds.
- PDS lookup returns HTTP 200.
- ITI-38 requests reach Xhuma.

**Outstanding External Dependency:**
- Downstream GP Connect requests returned HTTP 500 via the relay.
- Xhuma correctly returned an XDS Failure to Epic as a result.
- ITI-39 end-to-end acceptance remains blocked until GP Connect succeeds.
- *Note: This does not constitute complete clinical end-to-end acceptance at this time.*

## G. Separate Defect Discovered
- An ITI-38 failure `RegistryError` namespace appears incompatible with Epic error parsing.
- Epic received a Failure but presented "no error message was given".
- This is recorded as a follow-up item and will not be fixed in this work package.

## H. Lessons & Controls
- The immutable saved plan workflow performed as intended.
- The destructive guard successfully prevented unintended teardowns.
- Separate infrastructure and application gates effectively limited blast radius.
- Status-only Key Vault "Resolved" is insufficient evidence of the freshest secret content.
- Positive application-level trust evidence must be required after certificate rotation.
- Persistent runtime identities must not be derived from human workflow actors.
- Automated `/health` endpoints may be non-authoritative when runner connectivity is blocked; manual external verification remains required.

## I. Live PostgreSQL Audit Validation

Record the following verified facts from 6 October 2026.

### Access method

- Direct access from the operator workstation was unavailable because the PostgreSQL Flexible Server is private-networked through Private Link.
- No database networking, firewall rules, public access or SSH configuration was changed.
- `az webapp exec` was used to open an ephemeral shell inside the running INT App Service container.
- Database inspection was explicitly performed inside: `SET TRANSACTION READ ONLY`
- The transaction was rolled back after inspection.
- No row contents were modified.

### Database/schema state

Verified:
- database: `xhuma`
- transaction_read_only: `on`
- Alembic head: `1f53d4c82b1a`
- audit_event total rows: 1548
- minimum sequence: 1
- maximum sequence: 1548
- unique sequences: 1548
- non-monotonic rows: 0
- sequence gaps observed: 0

### Live transaction evidence

Within the preceding 90-minute INT test window:

- `pds_lookup` / `ok`: 6
- `sds_trace` / `ok`: 5
- `fhir_endpoint_trace` / `ok`: 5
- `gpconnect_request` / `fail` / `500`: 5
- `iti38_document_query` / `fail` / `UNKNOWN_ERROR`: 5

This is consistent with one PDS/ITI-55 interaction plus five ITI-38 transactions progressing through PDS/SDS/GP Connect before the known downstream GP Connect HTTP 500.

### Audit field validation

26 recent audit rows were inspected using metadata-only queries.

All 26 showed:
- `subject_ref` present
- `subject_ref` using expected `v1:` pseudonymous format
- SAML role present
- organisation present
- Purpose of Use present
- OpenTelemetry trace ID present

No raw `subject_ref` values, user identifiers, patient identifiers or detail payloads were printed during validation.

This provides live evidence that the deployed INT audit implementation is persisting the core audit identity/action/outcome metadata required for the current assurance scope.

### Correlation

- 21 of 26 recent rows did not contain `request_id`.
- This did not prevent transaction correlation because all 26 contained OpenTelemetry trace IDs.
- Record this as a correlation-quality hardening opportunity rather than an audit persistence failure.

### Production-hardening findings

1. **GP Connect response body persistence**

All five failed `gpconnect_request` audit rows contained the `detail.response_text` field.

A constrained numeric-token check found zero ten-digit numeric identifiers in the five stored details, but this MUST NOT be represented as proving absence of PHI.

Current source permits arbitrary downstream response text to be persisted in the audit DB. Record as a PRE-PRODUCTION data-minimisation hardening item:

- remove raw downstream `response_text` from audit persistence;
- retain only controlled metadata such as status code, transport and safe error classification;
- do not inspect or reproduce the existing stored response bodies.

2. **ITI-38 error classification**

The five failed `iti38_document_query` rows were stored with `error_code=UNKNOWN_ERROR` while the corresponding child GP Connect audit records correctly contained `error_code=500`.

Record as an audit-quality improvement: propagate a controlled upstream failure classification to the ITI-38 audit record.

3. **SOAP correlation**

The current SOAP handler parses the inbound WS-Addressing MessageID but the ITI-38 audit rows inspected did not contain `message_id`.

Record as an observability/audit-correlation improvement: preserve the appropriate SOAP MessageID/query correlation identifier in the ITI-38 audit event.
