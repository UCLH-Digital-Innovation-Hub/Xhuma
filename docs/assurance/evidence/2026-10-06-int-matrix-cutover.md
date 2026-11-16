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
- A source-controlled remediation of the container registry incident was introduced.
- The existing old image subsequently cold-started successfully, proving the root-cause remediation before a new candidate deployment.

## E. Final Deployment Execution
- Execution occurred in the Matrix Deployment Pilot.
- The reviewed non-destructive plan confirmed safeguards.
- The exact immutable candidate deployed successfully.
- Application startup was successful.
- Epic CA verification successfully loaded certificates.

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

## G. Post-Deployment Findings

A small number of interoperability, audit-quality and data-minimisation improvements were identified during testing. These are tracked separately from this public assurance record.

## H. Live PostgreSQL Audit Validation

Record the following verified facts from 6 October 2026.

### Access method

- Database inspection was explicitly performed using a read-only path.
- The transaction was rolled back after inspection.
- No row contents were modified.

### Database/schema state

Verified:
- database: `xhuma`
- transaction_read_only: `on`
- sequences were unique/monotonic with no observed gaps

### Live transaction evidence

Within the preceding INT test window:

- PDS/ITI-55 interactions plus ITI-38 transactions progressed through PDS/SDS/GP Connect before the known downstream GP Connect HTTP 500.

### Audit field validation

Recent audit rows were inspected using metadata-only queries.

They showed:
- pseudonymous subject reference present
- SAML role present
- organisation present
- Purpose of Use present
- OpenTelemetry trace ID present

No PHI or raw audit content was reproduced.

This provides live evidence that the deployed INT audit implementation is persisting the core audit identity/action/outcome metadata required for the current assurance scope.

## I. Functional Acceptance Follow-up — 7 October 2026

A subsequent INT interoperability test completed the ITI-38 document query and subsequent ITI-39 document retrieval workflow successfully.

This demonstrates that the downstream GP Connect failure observed on 6 October did not recur during this test and that the previously blocked end-to-end XDS document workflow was successfully exercised.

The successful test was performed against the existing deployed INT release. The later audit data-minimisation/correlation hardening present on `dev` had not yet been promoted to INT at the time of this functional test and is therefore not claimed as part of this evidence.

No patient-identifiable payload or raw clinical content is reproduced in this public assurance record.
