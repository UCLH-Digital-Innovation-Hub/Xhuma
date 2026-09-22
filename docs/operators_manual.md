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
We reuse the established INT bootstrap logic for new environments like `play`.

1. **Target Configuration**: Verify your target configuration in `infra/targets.json`.
2. **Execute Bootstrap**: Run the helper script locally to ensure the storage account exists:
   ```bash
   export AZURE_SUBSCRIPTION_ID="<your-subscription>"
   export AZURE_TENANT_ID="<your-tenant>"
   export XHUMA_TARGET="play"
   export XHUMA_RESOURCE_GROUP="rg-xhuma-play"
   bash infra/bootstrap/setup-target.sh
   ```
   This creates the state storage account and containers in the target resource group. `tfplans` automatically expires files after 7 days to prevent unbounded plan retention.

*(Note: Entra Blob authentication and OIDC are scheduled as separate, later hardening changes. We currently use interim storage-key authentication and long-lived Service Principal credentials.)*

---

## 3. Azure Service Principal & Permissions

To allow GitHub Actions to deploy infrastructure and code, Xhuma currently relies on a Service Principal with client secrets.

1. **Target Resource Group Permissions**: The SP requires `Contributor` rights over the target Azure Resource Group, and must be able to list storage account keys for the state backend.
2. **Shared Key Vault Access Prerequisite**: The Terraform configuration explicitly writes access policies to the shared key vault (`kv-xhuma-shared`) for the newly provisioned App Service identity and Locust Managed Identity. **The Service Principal must have `Key Vault Contributor` (or equivalent `Microsoft.KeyVault/vaults/accessPolicies/write` permissions) on the shared Key Vault.** This is an approved onboarding prerequisite.
3. **Expiry & Rotation**: Ensure the SP secret is rotated before expiry. Update the `AZURE_CLIENT_SECRET` in GitHub Secrets upon rotation.
4. **GitHub Secrets Configuration**:
   - `AZURE_CLIENT_ID`
   - `AZURE_CLIENT_SECRET`
   - `AZURE_TENANT_ID`
   - `AZURE_SUBSCRIPTION_ID`

## 2. Setting Up Variables

### Matrix Deployment Workflow

The deployment relies on specific GitHub environments to orchestrate the provisioning and rollout phases securely. 

**Environment Configuration:**

| GitHub environment    | Required secrets                                                                                                          |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `play-plan`           | Azure credential set; `CR_PAT`; `REGISTRY_ID`; `POSTGRES_PASSWORD`; `SHARED_KEY_VAULT_NAME`; `SHARED_RESOURCE_GROUP_NAME` |
| `rg-xhuma-play-infra` | Azure credential set                                                                                                      |
| `rg-xhuma-play`       | Azure credential set                                                                                                      |

*Note: The Azure credential set consists of `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `AZURE_TENANT_ID`, and `AZURE_SUBSCRIPTION_ID`. Do not duplicate Terraform input secrets in the apply/deploy environments, as apply consumes the saved plan.*

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
- `rehearsal/play-deployment` -> `play` environment
- `int` -> `int` environment
- `main` -> `prd` environments

### 5.1 First Deployment & Protected Plans
1. **Trigger**: Push code to the mapped branch (e.g., `rehearsal/play-deployment`).
2. **Plan Generation**: The workflow generates a Terraform plan and securely uploads it to the `tfplans` container in Azure Storage. Only a non-secret plan hash and summary are available in GitHub.
3. **Review & Approval**: An authorized operator must review the plan summary in GitHub (and the full plan in Azure Storage if necessary). Then, explicitly approve the infrastructure environment (`rg-xhuma-play-infra`).
4. **Image Deployment**: After infrastructure applies the inert bootstrap image, the pipeline deploys the exact scanned Docker image digest. This step requires a separate environment approval (`rg-xhuma-play`).

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
- [ ] Target configuration defined in `infra/targets.json`.
- [ ] Scoped Azure SP access configured.
- [ ] Bootstrap script executed to provision state storage.
- [ ] Code pushed to trigger the pipeline.
- [ ] Protected Terraform plan reviewed.
- [ ] GitHub Environment approvals granted for infra apply and container deployment.
- [ ] Local Key Vault populated with `epic-ca-cert`.
- [ ] Azure-side functional verification (e.g., checking Application Insights logs).
- [ ] Rollback exercise performed and documented.
