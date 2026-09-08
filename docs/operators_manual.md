# Xhuma Operator's Manual

**Document Purpose:** This manual provides a comprehensive, end-to-end runbook for deploying, configuring, and maintaining the Xhuma middleware across NHS Trust environments. 

---

## 1. Matrix Deployment Overview

Xhuma utilizes a **Shared-Nothing Matrix Deployment** strategy. This means every NHS Trust receives its own completely isolated cloud footprint to prevent cross-contamination of health data and limit the blast radius of any infrastructure failures.

- **Shared Resources:** A central Azure Resource Group hosts the Public JSON Web Key Set (JWKS) via Blob Storage (used by the NHS Spine to authenticate all Xhuma instances) and a Shared Key Vault for global secrets.
- **Trust-Local Resources:** Each Trust receives a dedicated Azure App Service, VNet, Managed Redis instance, and Local Key Vault provisioned via independent Terraform state files.

> **#TODO [Matrix Deployment]:** The fully automated CI/CD matrix pipeline is currently in development. Once live, this manual will be updated with the parameterized Terraform pipeline instructions for stamping out new environments automatically.

---

## 2. Azure Infrastructure & Authentication

To allow GitHub Actions to deploy infrastructure and code to Azure, Xhuma currently relies on an Azure Service Principal.

### 2.1 Service Principal Setup (Current)
You must create a Service Principal with `Contributor` rights over the target Azure Subscription or Resource Group.

1. **Create the Service Principal:**
   ```bash
   az ad sp create-for-rbac --name "xhuma-github-actions" --role contributor --scopes /subscriptions/<SUBSCRIPTION_ID> --sdk-auth
   ```
2. **Store the Output:** Copy the resulting JSON output. This will be used in Phase 4 as the `AZURE_CREDENTIALS` GitHub Secret.

> **#TODO [Security Modernization]:** Once the Matrix Deployment process is live, this process will migrate from long-lived Service Principal secrets to OpenID Connect (OIDC) Federated Credentials. This will allow GitHub Actions to authenticate directly against Azure Entra ID without storing static JSON credentials.

---

## 3. Key Vault Servicing

Xhuma relies on Azure Key Vault for all sensitive cryptographic material. Due to the matrix architecture, secrets are split between a **Shared Key Vault** (global) and a **Local Key Vault** (per-Trust).

### 3.1 Shared Key Vault (Global)
These secrets are shared across all Xhuma instances.

1. `jwtkey`: The 2048-bit RSA Private Key in PEM format. This is used by all Xhuma instances to sign JWTs for NHS Spine authentication.
2. `api-key`: An internal pre-shared key for Xhuma API access.
3. `dmd-client-secret`: The DM+D API secret.
4. `saml-trusted-issuer`: A pipe-separated string of trusted SAML Issuer CNs (e.g., `CN=EpicCA|CN=EpicTrustB`).

> **#TODO [Matrix Deployment - SAML Issuers]:** Currently, `saml-trusted-issuer` lives in the Shared Key Vault. However, distinct NHS Trusts running on separate Epic EHR instances will have entirely different Epic SAML Issuers. As part of the Matrix Deployment rollout, `SAML_TRUSTED_ISSUER` must be migrated out of the Shared Key Vault and into the Trust-specific Local Key Vault (similar to the Epic CA Cert) to ensure one Trust's issuer does not authorize access to another Trust's deployment.

### 3.2 Local Key Vault (Trust-Specific)
These secrets are completely isolated per Trust.

1. `epic-ca-cert`: The specific Trust's Epic Root CA certificate (Base64 PEM) used for mutual TLS (mTLS) validation. This guarantees that only that specific Trust's EHR can connect to their dedicated Xhuma instance.

**How to format PEM files for Azure Key Vault:**
Azure Key Vault UI often struggles with multi-line PEM files. You must compress the PEM into a single line string (removing the `\n` carriage returns) before pasting it into the Azure Portal. The Xhuma application layer (`app/security.py`) will automatically parse and re-add the 64-character line breaks upon startup.

---

## 4. GitHub Actions Secrets

To orchestrate the deployments, the following secrets must be configured in your GitHub Repository (or GitHub Environment):

| Secret Name | Purpose |
|-------------|---------|
| `AZURE_CREDENTIALS` | The JSON output from the Service Principal creation (Phase 2). |
| `DOCKER_REGISTRY_SERVER_USERNAME` | GitHub Container Registry (GHCR) username. |
| `DOCKER_REGISTRY_SERVER_PASSWORD` | GHCR Personal Access Token (PAT) with `read:packages` and `write:packages`. |
| `TF_API_TOKEN` | (Optional) Terraform Cloud API token if remote state is hosted externally. |

---

## 5. Trust-Specific Application Settings (tfvars)

While most application settings (like the GP Connect include flags) have sensible defaults built into the codebase, the following variables must be explicitly defined per-Trust. 

These are typically defined in the Trust's specific Terraform variables file (`infra/env/<trust>.tfvars`) which injects them as Environment Variables into the Azure App Service.

| Variable | Description |
|----------|-------------|
| `org_code` | The official NHS ODS Code for the Trust (e.g., `RRV00` for UCLH). This is used in PDS/SDS auditing and API headers. |
| `org_asid` | The Accredited System ID (ASID) assigned to the Trust's specific Epic instance by NHS Digital. |
| `env` | The environment name (e.g., `int`, `prd`). Controls conditional logic such as which NHS API base URLs to target. |
| `allowed_hosts` | A comma-separated list of domains allowed to connect to this instance. Must be restricted to the Trust's Epic outbound IPs/Domains in production. |
| `cors_origins` | A comma-separated list of allowed CORS origins. |
| `device_id` | A unique identifier representing the Xhuma/Epic system for NHS auditing purposes. |
| `require_mtls` | Must be set to `"true"` in production to enforce strict Epic mutual-TLS validation on inbound connections. |

---

## 6. Deployment & Provisioning

### 6.1 Infrastructure Provisioning
Run Terraform from the `infra/` directory to stand up the Trust environment. 
*Ensure your Terraform workspace/state file corresponds to the specific Trust you are deploying.*

```bash
cd infra/
terraform init
terraform plan -var-file="env/<trust>.tfvars"
terraform apply -var-file="env/<trust>.tfvars"
```

### 6.2 Application Deployment
Pushing to the `main` or designated environment branches will trigger the `.github/workflows/cd.yml` pipeline. This will:
1. Build the Docker Image.
2. Push the image to GHCR.
3. Trigger the Azure Web App to pull the latest image and restart.

---

## 7. Dashboards & Telemetry

Currently, the custom Xhuma operational dashboard (containing KQL charts for cache hit rates, API failures, and request latency) must be manually imported into the Azure Portal for each new Trust.

1. Navigate to the newly created Trust Resource Group in the Azure Portal.
2. Select **Dashboards**.
3. Click **Upload** and select the Xhuma JSON dashboard template.
4. Ensure the dashboard is bound to the Trust's specific Application Insights workspace.

> **#TODO [Matrix Deployment - Dashboards]:** The dashboard should be codified into the infrastructure pipeline using the `azurerm_portal_dashboard` Terraform resource. This will automatically provision and bind the dashboard to the correct Application Insights instance during the `terraform apply` phase, removing this manual step.

---

## 8. Operational Verification

After deployment, verify the health of the Xhuma instance.

### 8.1 Azure SSH Console
1. Navigate to the Azure Portal -> App Services -> `<trust-xhuma-app>`.
2. Under "Development Tools", select **SSH**.
3. Open a python REPL and verify Key Vault resolution and VNet outbound connectivity to the NHS:
   ```python
   from app.pds.pds import get_pds_token
   print(get_pds_token()) 
   # Should print a valid NHS access token.
   ```

### 8.2 Application Insights
1. Navigate to the Application Insights workspace tied to the App Service.
2. Check the **Failures** blade.
3. Verify there are no `AccessToKeyVaultDenied` or `MalformedFraming` startup errors.
4. Search the Transaction Search for inbound Epic requests (ITI-55, ITI-38, ITI-39) to verify mTLS handshakes and SAML validation are succeeding.

---

## 9. Matrix Deployment Pilot Rehearsal

To execute a controlled deployment rehearsal against the `play` environment using the new matrix strategy, follow these steps:

### 9.1 Platform Bootstrap
Before the pipeline can run, the state storage for the `play` environment must be provisioned.
1. Run `infra/bootstrap/setup-play.sh` from your local machine.
2. This creates `rg-xhuma-play` (resource group), `rg-xhuma-state-play` (state resource group), and `xtfrgxhumaplay` (storage account).

### 9.2 Interim Service Principal Setup
For the pilot phase, we will continue to use the existing Service Principal with client secrets. Ensure the following GitHub Secrets are configured at the repository or environment level:
- `AZURE_CLIENT_ID`
- `AZURE_CLIENT_SECRET`
- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`

*Note: OIDC migration is deferred until after this pilot phase.*

### 9.3 Triggering the Rehearsal
The matrix pipeline (`matrix-deploy.yml`) is currently restricted to the pilot branch.
1. Commit your changes to `feat/matrix-deployment-pilot`.
2. Push the branch to GitHub:
   ```bash
   git push origin feat/matrix-deployment-pilot
   ```
3. Pushing to this exact branch will trigger the pipeline, automatically mapping the deployment to the `play` target. 
4. The pipeline will build the candidate image, run CI, plan the infrastructure, and pause.
5. An authorized operator must explicitly approve the `rg-xhuma-play-infra` and `rg-xhuma-play` environments in the GitHub Actions UI before infrastructure is applied and the container is deployed.

### 9.4 Rollback & Recovery Steps
If the deployment fails or introduces regressions:
1. Re-run an older, successful GitHub Actions run of the matrix deploy workflow to restore the previous container digest.
2. The infrastructure plan uses `ignore_changes` on the Docker image, ensuring a subsequent Terraform run will not overwrite the rollback image.
3. Database migrations (Alembic) are executed at container startup. If a rollback is required, ensure the previous image's code is compatible with any new database schema changes applied during the rehearsal.

### 9.5 Remaining Configuration Dependencies
Before this workflow is adopted for `INT` or `main`:
- OIDC Service Principal authentication must be implemented.
- Shared-vault access policies must be migrated or updated to ensure targets do not inadvertently inherit excessive permissions.
- The `matrix-deploy.yml` orchestrator must be expanded to handle multi-ring rollouts and `fail-fast: false` aggregation.
- Existing `cd.yml` and `infra.yml` workflows must be deprecated in a coordinated cutover.
