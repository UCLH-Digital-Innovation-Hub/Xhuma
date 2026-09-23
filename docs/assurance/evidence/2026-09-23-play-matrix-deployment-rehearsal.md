# Play Matrix Deployment Rehearsal (23 September 2026)

## 1. Evidence Metadata

* **Repository:** `UCLH-Digital-Innovation-Hub/Xhuma`
* **Branch:** `rehearsal/play-deployment`
* **Workflow Run:** Run 26, attempt 2
* **Commit SHA:** `04c4d10942596c7924ffaff78029a062ed282331`
* **Date:** 23 September 2026

## 2. Scope and Purpose
This document captures factual evidence from the deployment rehearsal demonstrating the safeguards built into the Xhuma CI/CD and runtime architecture. This is an immutable assurance evidence record of the observed execution sequence, controls demonstrated, and resulting system state.

## 3. Deployment Architecture Exercised
* **Target:** `play`
* **Azure target resource group:** `rg-xhuma-play`
* **Shared resource group:** `rg-xhuma-shared`
* **Deployment identity:** `xhuma-github-actions-int`

## 4. Controls Demonstrated

| Control | Threat / Failure Mitigated | Evidence Observed | Outcome |
| :--- | :--- | :--- | :--- |
| **Identity Scoping (RBAC)** | Deployment identity mutating unauthorized production resources. | `xhuma-github-actions-int` successfully deployed to `rg-xhuma-play` and read from `rg-xhuma-shared` but lacked access to the UCLH production resource group. | **Implemented Control** / **Observed** |
| **Manifest & Plan Validation** | Applying tampered, malformed, or unintended infrastructure plans. | The saved plan was bound to the commit, run, target, tenant, backend coordinates, and candidate image digest. Verified via SHA-256 before Apply. | **Implemented Control** / **Observed** |
| **GitHub Environments** | Unauthorized deployment from the rehearsal branch. | Deployment paused and required explicit authorization to proceed to the `play` environment. | **Implemented Control** / **Observed** |
| **Fail-Closed Runtime Configuration** | Application starting and serving traffic in an insecure or misconfigured state. | Application refused to start, throwing `RuntimeError` due to unresolved `@Microsoft.KeyVault(...)` reference for `API_KEY`. | **Implemented Control** / **Observed** |
| **Key Vault Network Isolation** | Unauthorized network access to the shared Key Vault. | The shared vault had `DefaultAction: Deny` and blocked the Play App Service integration subnet, yielding `AccessToKeyVaultDenied`. | **Implemented Control** / **Observed** |
| **Target-Local Operational Secrets** | Missing target-local operational secrets (e.g. `EPIC_CA_CERT`). | `EPIC_CA_CERT` must be populated prior to clinical workflows. (Not the immediate startup blocker). | **Remaining Operator Prerequisite** |

## 5. Chronological Rehearsal Evidence

1. **Build & Scan:** CI and tests passed. The container built successfully, and Trivy security scanning passed.
2. **Terraform Target Selection:** Target selection and schema validation passed.
3. **Azure Authentication:** Azure authentication initially failed when an incorrect client-secret pair was configured.
4. **RBAC Correction:** Scoped RBAC was corrected. The INT deployment identity was granted `Contributor` access solely on `rg-xhuma-int`, `rg-xhuma-shared`, and `rg-xhuma-play`. The UCLH production resource group was deliberately outside the identity's scope.
5. **Terraform Plan:** Successfully authenticated into Play and used the configured remote backend.
6. **Manifest Validation Bug & Fix:** Manifest validation initially stopped Apply due to a repository-relative backend path resolution bug. The validator was surgically fixed without weakening integrity controls.
7. **Terraform Apply:** The saved Terraform plan was verified (associated with repository, commit SHA, workflow run, target, tenant/subscription, backend coordinates, and candidate image digest) and protected with SHA-256 verification. Apply succeeded.
8. **Application Deployment:** GitHub Environment deployment rules prevented deployment from the rehearsal branch until the branch was explicitly authorised. Following authorisation, application deployment succeeded.
9. **Database Migrations:** Alembic database migrations ran successfully.
10. **Application Startup Failure:** The application failed closed during startup due to an unresolved Key Vault reference.

## 6. Fail-Closed Behaviour Observed

Following the successful container deployment, the Play App Service attempted to resolve its `@Microsoft.KeyVault(...)` references using its system-assigned managed identity. 

The observed Azure evidence was:
* The shared vault uses the access-policy model (`RbacEnabled: false`).
* The Play App Service managed identity had `Get` and `List` secret permissions.
* The shared vault had its network ACL set to `DefaultAction: Deny`.
* The virtual network allow-list contained the existing INT app subnet but not the Play app subnet.

Therefore, App Service Key Vault reference resolution was denied by the shared-vault network boundary, resulting in a reference status of `AccessToKeyVaultDenied`. (Secret existence within the vault was not established by this error and is not asserted here).

Because resolution failed, the literal unresolved `@Microsoft.KeyVault(...)` value remained in the application configuration. Xhuma startup validation detected the unresolved reference, and the application explicitly terminated rather than serving traffic, yielding the following runtime evidence:

```text
RuntimeError: Unresolved KeyVault reference for required configuration: API_KEY
```

This sequence demonstrates the engineered fail-closed paradigm.

## 7. Azure Permission Boundaries
The deployment identity (`xhuma-github-actions-int`) operated strictly within its explicitly authorized scope (`rg-xhuma-play`, `rg-xhuma-int`, and `rg-xhuma-shared`). The UCLH production resource group remained securely out of scope, providing observed evidence of the intended RBAC isolation of the matrix deployment architecture.

## 8. Artefact and Terraform-plan Integrity Controls
The matrix deployment workflow demonstrated cryptographic plan-integrity verification and metadata binding of the Terraform plan. The plan was bound to the source commit (`04c4d10942596c7924ffaff78029a062ed282331`), the specific workflow run, and the `play` target. Prior to execution, the downloaded plan was verified against its SHA-256 digest, proving the integrity of the state and intent before infrastructure mutation.

## 9. GitHub Environment Protections
The rehearsal branch successfully triggered the workflow, but the physical deployment was appropriately intercepted by GitHub Environment protection rules. Deployment to the Azure App Service was paused and blocked until an authorized approver explicitly permitted the rollout.

## 10. Runtime Configuration Safeguards
The Xhuma middleware correctly enforces a fail-closed paradigm. Rather than booting into a partially configured or insecure state, the application detected the unresolved `API_KEY` reference and immediately terminated with a `RuntimeError`.

## 11. Residual Prerequisites before Functional Play Testing
Before functional clinical testing can commence in the `play` environment, an authorized operator must fulfill the remaining manual prerequisites:
1. Allow the target App Service integration subnet through the shared Key Vault network ACL.
2. Refresh/re-resolve App Service Key Vault references and confirm they report `Resolved`.
3. Populate the target-local `EPIC_CA_CERT` before mTLS/clinical workflow testing.
4. Perform post-deployment functional verification.

## 12. Evidence Conclusion
The 23 September 2026 rehearsal successfully validated the Xhuma matrix deployment pipeline. It demonstrated secure Azure authentication, strict identity scoping, cryptographic plan validation, environment approval gating, and fail-closed runtime configuration behavior. Full operational assurance, production readiness, and clinical safety approval are explicitly excluded from the scope of this rehearsal and remain subject to separate verification.
