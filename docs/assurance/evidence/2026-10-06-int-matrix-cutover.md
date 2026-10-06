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
