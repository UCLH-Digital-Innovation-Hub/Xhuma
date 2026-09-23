# Xhuma Operator's Manual

**Document Purpose:** This manual provides a comprehensive, end-to-end runbook for deploying, configuring, and maintaining the Xhuma middleware across NHS Trust environments, including the `play` environment rehearsal.

---

## 1. Matrix Deployment Overview

Xhuma utilizes a **Shared-Nothing Matrix Deployment** strategy. Every target environment (e.g., `play`, `int`, production trusts) receives its own isolated cloud footprint to prevent cross-contamination of health data and limit blast radius.

- **Shared Resources:** A central Azure Resource Group hosts the Public JSON Web Key Set (JWKS) via Blob Storage and a Shared Key Vault for global secrets (e.g., API keys, DM+D secrets).
- **Target-Local Resources:** Each environment receives a dedicated Azure App Service, VNet, Managed Redis, PostgreSQL, and Local Key Vault.

---

## 2. Infrastructure Bootstrapping and State Storage

Terraform state and reviewed execution plans are stored securely in Azure Blob Storage. Each target has its own storage account within its target resource group.

### 2.1 Reused Bootstrapping Procedure
We reuse the established bootstrap logic across environments. For `play`, we now utilize a target-agnostic script.

1. **Target Configuration**: Verify your target configuration in `infra/targets.json` and backend coordinates in `infra/backends/play.hcl`.
2. **Execution**: The `matrix-deploy.yml` workflow automatically runs the bootstrap script:
   ```bash
   export AZURE_SUBSCRIPTION_ID="<your-subscription>"
   export AZURE_TENANT_ID="<your-tenant>"
   export XHUMA_BACKEND_FILE="infra/backends/play.hcl"
   bash infra/bootstrap/setup-target.sh
   ```
   This creates the state storage account and containers in the target resource group. `tfplans` automatically expires files after 7 days to prevent unbounded plan retention. It is important to note that this step performs writes to Azure Storage (creating the storage account and blob containers) *before* the infrastructure plan is even generated or approved.

*(Note: Entra Blob authentication and OIDC are scheduled as separate, later hardening changes. We currently use interim storage-key authentication and long-lived Service Principal credentials.)*

---

## 3. Azure Service Principal & Permissions

To allow GitHub Actions to deploy infrastructure and code, Xhuma currently relies on a Service Principal with client secrets.

1. **Target Resource Group Permissions**: The SP requires `Contributor` rights over the target Azure Resource Group, and must be able to list storage account keys for the state backend.
2. **Shared Key Vault Access Prerequisite**: The Terraform configuration explicitly writes access policies to the shared key vault (`xhuma-shared-kv-int`) for the newly provisioned App Service identity and Locust Managed Identity. **The Service Principal must have `Key Vault Contributor` (or equivalent `Microsoft.KeyVault/vaults/accessPolicies/write` permissions) on the shared Key Vault.** This is an approved onboarding prerequisite.
3. **Expiry & Rotation**: Ensure the SP secret is rotated before expiry. Update the `AZURE_CLIENT_SECRET` in GitHub Secrets upon rotation.
4. **GitHub Secrets Configuration**:
   - `AZURE_CLIENT_ID`
   - `AZURE_CLIENT_SECRET`
   - `AZURE_TENANT_ID`
   - `AZURE_SUBSCRIPTION_ID`
   - `SHARED_SUBSCRIPTION_ID`

## 2. Setting Up Variables

### Matrix Deployment Workflow

The deployment relies on specific GitHub environments to orchestrate the provisioning and rollout phases securely. 

**Environment Configuration:**

| GitHub environment    | Required secrets                                                                                                          |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `play-plan`           | Azure credential set; `CR_PAT`; `REGISTRY_ID`; `POSTGRES_PASSWORD`; `SHARED_KEY_VAULT_NAME`; `SHARED_RESOURCE_GROUP_NAME`; `SHARED_SUBSCRIPTION_ID` |
| `rg-xhuma-play-infra` | Azure credential set                                                                                                      |
| `rg-xhuma-play`       | Azure credential set                                                                                                      |

*Note: The Azure credential set consists of `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `AZURE_TENANT_ID`, and `AZURE_SUBSCRIPTION_ID`. Do not duplicate Terraform input secrets in the apply/deploy environments, as apply consumes the saved plan.*

**Shared Resources Configuration:**
The `SHARED_SUBSCRIPTION_ID` is `c24b0c3e-9e09-4c7c-8687-75e8b654bc8e`. This cross-subscription variable must be explicitly provided to the environments running Terraform Plan (e.g., `play-plan` for matrix deployments) and the legacy `infra.yml` workflow, which provisions INT and production infrastructure.

**Actual Deployment Sequence:**

1. **Configure GitHub Environments & Secrets:** Ensure the environments (`play-plan`, `rg-xhuma-play-infra`, `rg-xhuma-play`) exist and their secrets are securely stored. Environment names alone do not configure protection; you must set manual approvers on `rg-xhuma-play-infra` and `rg-xhuma-play`.
2. **Automatic Bootstrap and Plan:** The workflow automatically runs the bootstrap script to create state storage (if missing), then executes `terraform plan`. The generated plan is uploaded securely and its hash is presented for review.
3. **Review and Approved Apply:** Operators review the plan output. Once approved, the apply job verifies the plan hash and provisions the infrastructure.
4. **Local-Vault Onboarding:** With the infrastructure provisioned, the operator manually injects required operational secrets (e.g., `epic-ca-cert`) into the newly created target Key Vault.
5. **Approved Application Deployment:** Once the vault is ready, the deployment job replaces the inert bootstrap image with the actual application container digest.
6. **Verification and Manual Rollback:** The new image digest is verified. If issues occur, operators manually roll back by deploying a previous known-good digest, ensuring no concurrent deployment or database incompatibility.

---

## 4. Key Vault Population

Before functional verification can succeed, the environment's local Key Vault must be populated by an operator.

1. `epic-ca-cert`: The target-specific Epic Root CA certificate (Base64 PEM) used for mutual TLS (mTLS).
2. **Populating the Vault**: Use the Azure Portal or CLI to add the secret to the newly provisioned Local Key Vault (e.g., `kv-xhuma-play-...`).
   *Note: Ensure multi-line PEM files are formatted correctly (newlines replaced if pasting into the Azure Portal).*

---

## 5. Deployment Orchestration

Deployment is handled by GitHub Actions (`.github/workflows/matrix-deploy.yml`), which enforces strict boundaries:
- `matrix-deploy.yml` currently orchestrates only the `play` environment from the `rehearsal/play-deployment` branch.
- Legacy pipelines (`cd.yml` and `infra.yml`) still own the deployment to `int` and `prd` from the `int` and `main` branches.

### 5.1 First Deployment & Protected Plans
1. **Trigger**: Push code to the mapped branch (e.g., `rehearsal/play-deployment`).
2. **Plan Generation**: The workflow generates a Terraform plan and securely uploads it to the `tfplans` container in Azure Storage. Only a non-secret plan hash and summary are available in GitHub. Plan generation will fail if a plan already exists for that run.
3. **Review & Approval**: An authorized operator must review the plan summary in GitHub (and the full plan in Azure Storage if necessary) using the strict review hierarchy below. Then, explicitly approve the infrastructure environment (`rg-xhuma-play-infra`).

#### Terraform Plan Review Hierarchy
When reviewing an immutable saved plan for approval, operators must follow this strict hierarchy to prevent accidental disruption and avoid exposing sensitive state data:

**A. Review headline counts:** Check the high-level summary (e.g., `X to add, Y to change, Z to destroy`).
**B. Review changed resource addresses/actions:** Identify exactly which resources are being modified using the immutable saved plan.
   ```bash
   terraform show -json tfplan | jq '.resource_changes[] | {address, actions: .change.actions}'
   ```
**C. Inspect changed ATTRIBUTE PATHS only:** If a resource change is unexplained, inspect which specific attributes are changing, without looking at the values.
   ```bash
   # Example: extracting just the paths of changed attributes
   terraform show -json tfplan | jq '.resource_changes[] | select(.change.actions != ["no-op"]) | {address, paths: (if .change.after_unknown then (.change.after_unknown | keys) else [] end) + (if .change.after then (.change.after | keys) else [] end)}'
   ```
**D. Selectively inspect non-sensitive before/after values:** If still unexplained, only inspect attributes known to be non-sensitive.
**E. Never dump the complete JSON Terraform plan:** Do not dump the plan into GitHub logs or documentation because Terraform plans may contain sensitive values.

> **Example (Play Rehearsal, Sept 2026):**
> A superficially safe plan showed: `0 to add, 15 to change, 0 to destroy`. Resource-level inspection looked non-destructive. However, attribute-path inspection revealed that Terraform intended to remove externally-managed organisational tags (e.g., CostCenter), Azure-managed integration metadata (Application Insights hidden links), and an existing subnet service endpoint (`Microsoft.Storage`). The Apply was rightfully withheld, and the Terraform ownership model was corrected via `ignore_changes` instead of blindly applying the drift.

4. **Plan Retries & Expiry**: If the apply step fails, it can be retried and will re-download the exact same plan blob securely. Plans expire automatically after 7 days in Blob Storage. If a plan is no longer valid, a completely new workflow run is required to generate and approve a new plan.
5. **Image Deployment**: After infrastructure applies the inert bootstrap image, the pipeline deploys the exact scanned Docker image digest. This step requires a separate environment approval (`rg-xhuma-play`).

### 5.2 Digest Rollback & Recovery
Deployment is deterministic. We record the previous digest before deploying and the new digest after.

1. **Recovery Ownership**: If a deployment introduces regressions, authorized operators can perform a rollback.
2. **Rollback Procedure**: 
   - Identify the previous known-good digest from the deployment step summary.
   - Manually trigger a recovery deployment via Azure CLI or a dedicated rollback workflow using that specific digest:
     ```bash
     az webapp config container set --name <app_service> --resource-group <rg> --docker-custom-image-name ghcr.io/...@sha256:...
     ```
   - Ensure older application code is compatible with the current Alembic database schema migrations.

---

## 6. Verification and Health Checks

### 6.1 Safe Verification Boundaries
- **Liveness Probe**: The `/health` endpoint is unauthenticated and returns a coarse HTTP 200 process-liveness signal. It does not leak secrets, tokens, or perform downstream NHS requests.
- **Protected Readiness**: Startup configuration, database, and relay status are checked via Azure App Service health monitoring and Azure-side operational probes, rather than exposing an unauthenticated diagnostic endpoint.
- **Manual Clinical Check**: Because the GitHub Actions runner does not possess the required mTLS certificates, a manual synthetic test must be run from a trusted clinical workstation to verify SOAP mTLS and audit capabilities after deployment.

### 6.2 Operator Checklist for New Environments

**Implemented Readiness Checks (Automated):**
- [ ] Application liveness probe (HTTP 200).
- [ ] Status-only Key Vault resolution check (if supported by access model).
- [ ] Digest verification of the deployed container.

**Manual Prerequisites (To be done by Operator):**
- [ ] Target configuration defined in `infra/targets.json`.
- [ ] Scoped Azure SP access configured and credentials placed in GitHub.
- [ ] GitHub Environment approvals configured for infra apply and container deployment.
- [ ] Local Key Vault populated with `epic-ca-cert`.
- [ ] App Service integration subnet ID added to `infra/shared/env/shared.tfvars` (Terraform-managed shared Key Vault network ACL onboarding).

**Follow-ups / Manual Exercises:**
- [ ] Trust-local authentication.
- [ ] Audit retention/access policies.
- [ ] Restore evidence.
- [ ] Key rotation.
- [ ] Run synthetic manual clinical check (SOAP/mTLS) from a trusted workstation.
- [ ] Rollback exercise performed and documented.

### 6.3 Key Vault Reference Verification

A mandatory verification step must be performed post-Terraform and pre-functional-testing to ensure the App Service can resolve its `@Microsoft.KeyVault(...)` configuration references. Note that Terraform automatically provisions the App Service managed-identity access policy on the shared Key Vault.

When the shared Key Vault is configured with `DefaultAction = Deny`, the target App Service integration subnet must explicitly be permitted by the vault's network ACL.

**Verification Sequence:**
1. Confirm the App Service system-assigned managed identity exists.
2. Confirm the required secret permissions/access policy exist on the shared vault.
3. Confirm the target App Service subnet is permitted by the shared vault's network ACL.
4. Confirm the App Service Key Vault reference status is `Resolved`.
5. **Do not proceed** to functional testing if the reference status is `AccessToKeyVaultDenied`, `SecretNotFound`, or any other unresolved state.

---

## 7. Shared Infrastructure Adoption (Terraform)

When managing shared infrastructure components (such as the shared Key Vault `xhuma-shared-kv-int` network ACLs) in Terraform:
- Ensure the resource includes a `lifecycle { prevent_destroy = true }` block.
- **Note:** `prevent_destroy` does not guarantee Terraform will never *propose* replacement. Instead, any proposed destruction or replacement of the imported shared Key Vault will be blocked by `prevent_destroy` during the apply phase.
- Therefore, the initial reconciliation plan must be clean and contain no proposed destruction or replacement of the vault before any apply is attempted.

---

## 7. Assurance and Evidence Records

Specific deployment rehearsals and assurance events are captured as immutable evidence records. These records are retained separately from this living operational runbook to preserve point-in-time factual observations.

- [Play Matrix Deployment Rehearsal (23 September 2026)](./assurance/evidence/2026-09-23-play-matrix-deployment-rehearsal.md)
