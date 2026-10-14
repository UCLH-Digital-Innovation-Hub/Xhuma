# Xhuma Operator's Manual

**Document Purpose:** This manual provides a comprehensive, end-to-end runbook for deploying, configuring, and maintaining the Xhuma middleware across NHS Trust environments, including the `play` environment rehearsal.

**Security Rule for Operators:**
> [!WARNING]
> **Screenshots and Logs:** Operational evidence must not contain credentials, tokens, patient data, certificate material, secret-valued configuration or protected infrastructure identifiers.

> **Note:** Screenshots included in this manual are illustrative evidence captured during the September 2026 Play rehearsal. Commands and configuration in this runbook are authoritative; UI screenshots may change as GitHub and Azure evolve.

> **Tired? in a hurry?** See the [Xhuma Deployment for Tired Humans](./deployment_for_tired_humans.md) quick-start guide.

---

## Table of Contents
1. [Architecture Overview](#1-architecture-overview)
2. [Infrastructure Bootstrapping and State Storage](#2-infrastructure-bootstrapping-and-state-storage)
3. [Azure Service Principal & Permissions](#3-azure-service-principal--permissions)
4. [Setting Up Variables](#4-setting-up-variables)
5. [Key Vault Population](#5-key-vault-population)
6. [Deployment Orchestration](#6-deployment-orchestration)
7. [Verification and Health Checks](#7-verification-and-health-checks)
   - [7.8 GP Connect Commissioning Verification and Domain Readiness](#78-gp-connect-commissioning-verification-and-domain-readiness)
8. [Shared Infrastructure Adoption (Terraform)](#8-shared-infrastructure-adoption-terraform)
9. [Quick Operator Commands](#9-quick-operator-commands)
   - [Azure Runtime Diagnostics and Audit Verification](#azure-runtime-diagnostics-and-audit-verification)
   - [PDS to SDS to GP Connect Troubleshooting Flow](#pds-to-sds-to-gp-connect-troubleshooting-flow)
   - [Cache-Aware Diagnosis and Selective Invalidation](#cache-aware-diagnosis-and-selective-invalidation)
   - [SDS Bundle Resolution and Current Error Semantics](#sds-bundle-resolution-and-current-error-semantics)
   - [Audit Trail Interpretation Caveats](#audit-trail-interpretation-caveats)
   - [Log Analytics Telemetry Queries](#log-analytics-telemetry-queries)
   - [Private PostgreSQL Audit Verification](#private-postgresql-audit-verification)
10. [Assurance and Evidence Records](#10-assurance-and-evidence-records)

---

## 1. Architecture Overview

Xhuma utilises a **Target-isolated matrix deployment with centrally managed shared services**. Every target environment (e.g., `play`, `int`, production trusts) receives its own isolated cloud footprint for compute and data to prevent cross-contamination of health data and limit blast radius. 

- **Shared Resources:** A centrally managed Azure Resource Group hosts shared services with separate lifecycle/ownership, such as the Public JSON Web Key Set (JWKS) via Blob Storage and a Shared Key Vault for global secrets (e.g., API keys, DM+D secrets).
- **Target-Local Resources:** Each environment receives a dedicated Azure App Service, VNet, Managed Redis, PostgreSQL, and Local Key Vault.

---

## 2. Infrastructure Bootstrapping and State Storage

Terraform state and reviewed execution plans are stored securely in Azure Blob Storage. Each target has its own storage account within its target resource group.

### 2.1 Reused Bootstrapping Procedure
We reuse the established bootstrap logic across environments. For `play`, we now utilise a target-agnostic script.

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

1. **Target Resource Group Permissions**: The SP requires least privilege rights over the target Azure Resource Group and state backend.
2. **Shared Key Vault Access Prerequisite**: The Terraform configuration securely manages access policies on the shared Key Vault. The Service Principal requires sufficient rights to manage these policies.
3. **Expiry & Rotation**: Ensure the deployment credential is rotated before expiry. Update the relevant GitHub Secret upon rotation.

---

## 4. Setting Up Variables

### 4.1 Matrix Deployment Workflow

The deployment relies on specific GitHub environments to orchestrate the provisioning and rollout phases securely. 

![Play matrix deployment pipeline](./assets/play-plan-approval-gate.png)
*Figure 1 — Pre-plan approval gate — Run #32 paused before `Infra Plan - play` because the `play-plan` GitHub Environment required reviewer approval.*

**Environment Configuration:**

Deployments use protected GitHub Environments and dedicated Azure deployment identities. Target-specific credentials, role assignments, environment configuration and approval topology are maintained in the restricted operational record and are intentionally not reproduced in this public runbook.

**Shared Resources Configuration:**
The shared subscription ID must be explicitly provided to the environments running Terraform Plan (e.g., `<target>-plan` for matrix deployments) and the legacy `<legacy-workflow>.yml` workflow, which provisions legacy infrastructure.

**Actual Deployment Sequence:**

1. **Configure GitHub Environments & Secrets:** Ensure the environments exist and their credentials are securely stored with appropriate manual approver configurations.
2. **Automatic Bootstrap and Plan:** The workflow automatically runs the bootstrap script to create state storage (if missing), then executes `terraform plan`. The generated plan is uploaded securely and its hash is presented for review.
3. **Review and Approved Apply:** Operators review the plan output. Once approved, the apply job verifies the plan hash and provisions the infrastructure.
4. **Local-Vault Onboarding:** With the infrastructure provisioned, the operator manually injects required operational secrets (e.g., `epic-ca-cert`) into the newly created target Key Vault.
5. **Approved Application Deployment:** Once the vault is ready, the deployment job replaces the inert bootstrap image with the actual application container digest.
6. **Verification and Manual Rollback:** The new image digest is verified. If issues occur, operators manually roll back by deploying a previous known-good digest, ensuring no concurrent deployment or database incompatibility.

---

## 5. Key Vault Population

Infrastructure and application liveness can be established before the target-local Epic CA is populated. However, `epic-ca-cert` is required before Epic/SOAP/mTLS functional acceptance.

1. `epic-ca-cert`: The target-specific Epic verification CA bundle required by current application semantics (Base64 PEM) used for mutual TLS (mTLS). It may contain the direct issuing intermediate(s).
2. **Populating the Vault**: Use the Azure Portal or CLI to add the secret to the newly provisioned Local Key Vault (e.g., `<app_service_name>-kv`).
   *Note: Ensure multi-line PEM files are formatted correctly (newlines replaced if pasting into the Azure Portal).*

---

## 6. Deployment Orchestration

Deployment is handled by GitHub Actions (`.github/workflows/matrix-deploy.yml`), which enforces strict boundaries:
- The matrix workflow (`matrix-deploy.yml`) is the authoritative deployment path for `int` and the `play` environment.
- The legacy `infra.yml` remains present for the production/legacy path and retains a manual `workflow_dispatch` entry point. It must not be manually dispatched against `int`; this path is pending retirement or hardening.

### 6.1 Continuous Integration and Build Controls

Before any deployment plan is generated, the pipeline enforces strict quality and security gates:

![Play CI tests](./assets/play-ci-tests.png)
*Figure 2 — CI & Tests stage confirming all tests pass before proceeding.*

![Play Build Security](./assets/play-build-security.png)
*Figure 3 — Build & Push stage showing the immutable image build and Trivy vulnerability scan.*

### 6.2 First Deployment & Protected Plans
1. **Trigger**: Push code to the mapped branch (e.g., `rehearsal/play-deployment`).
2. **Plan Generation**: The workflow generates a Terraform plan and securely uploads it to the `tfplans` container in Azure Storage. Only a non-secret plan hash and summary are available in GitHub. Plan generation will fail if a plan already exists for that run.
3. **Review & Approval**: An authorized operator must review the plan summary in GitHub (and the full plan in Azure Storage if necessary) using the strict review hierarchy below. Then, explicitly approve the infrastructure environment (e.g., `<rg-target-infra>`).

### 6.3 Terraform Plan Review Before Approval

When reviewing an immutable saved plan for approval, operators must follow this COMPLETE worked procedure to prevent accidental disruption and avoid exposing sensitive state data.

**Explicit STOP Conditions:**
Do NOT proceed if you observe any of the following:
- Plan SHA mismatch
- Wrong target/subscription/commit
- Unexplained destroy/replacement
- Unexplained resource or attribute drift
- Unexpected shared/production resources in the plan
- Unresolved Key Vault references
- Failed health/digest verification

**Step-by-step Review Procedure:**

1. **Select the target Azure subscription:**
   ```bash
   az account set --subscription "<target-subscription-id>"
   ```
2. **Securely obtain the plan storage credentials:**
   ```bash
   RG_NAME="<backend-resource-group>"
   SA_NAME="<backend-storage-account>"
   # Obtain credentials securely via Azure AD or approved operational method
   ```
3. **Download the exact immutable `.tfplan`:**
   ```bash
   PLAN_FILE="<plan-filename>"
   az storage blob download --auth-mode login --account-name "$SA_NAME" --container-name tfplans --name "$PLAN_FILE" --file "/tmp/$PLAN_FILE"
   ```
4. **SHA256 verification against the workflow manifest/job summary:**
   ```bash
   sha256sum "/tmp/$PLAN_FILE"
   # Compare the output hash with the manifest or GitHub Actions job summary. STOP if SHA differs.
   ```
   *(Note: The plan hash verification is automatically performed by the CI pipeline, but operators should verify manually if performing manual applies).*

5. **Determine and install the exact Terraform version used by the workflow:**
   Check the workflow file for the pinned version (e.g., `1.5.7`). If Cloud Shell differs, temporarily install it.
6. **Checkout the exact source commit SHA:**
   ```bash
   git checkout <commit-sha>
   ```
7. **Initialise Terraform locally without connecting to remote state:**
   ```bash
   terraform init -backend=false -input=false
   ```
8. **Review headline counts and changes against the immutable plan:**
   Run `terraform show -json` against the immutable plan to review the changes. Check the high-level summary (`X add, Y change, Z destroy`).
   
   **Extract changed resource addresses/actions:**
   ```bash
   terraform show -json "/tmp/$PLAN_FILE" | jq '.resource_changes[] | {address, actions: .change.actions}'
   ```
   *(Note: This safe summary is also printed in the GitHub Actions step summary).*

9. **Extract changed attribute PATHS only:** 
   If a resource change is unexplained, inspect which specific attributes are changing, without looking at the values using the exact tested jq command:
   ```bash
   terraform show -json "/tmp/$PLAN_FILE" | jq -r '
     .resource_changes[]
     | select(.change.actions != ["no-op"])
     | .address as $addr
     | (.change.before // {}) as $before
     | (.change.after // {}) as $after
     | ([($before | paths(scalars)), ($after | paths(scalars))] | unique[]) as $p
     | select(($before | getpath($p)) != ($after | getpath($p)))
     | "\($addr)\t\($p | map(tostring) | join("."))"
   '
   ```
10. **Selectively inspect non-sensitive before/after values:** If still unexplained, only inspect attributes known to be non-sensitive. Never dump the complete JSON Terraform plan into GitHub logs or documentation.

> **Example (Play Rehearsal, Sept 2026):**
> A superficially safe plan showed: `0 to add, 15 to change, 0 to destroy`. 
>
> ![Play Plan Summary](./assets/play-plan-summary.png)
> *Figure 4 — Play rehearsal plan showing 0 add / 15 change / 0 destroy. Further inspection revealed unexplained drift and Apply was withheld.*
> 
> Resource-level inspection looked non-destructive (in-place updates). However, attribute-path inspection revealed that Terraform intended to remove externally-managed organisational tags, Azure-managed integration metadata (Application Insights hidden links), and an existing DB subnet `Microsoft.Storage` service endpoint. The Apply was rightfully withheld, and the Terraform ownership configuration was corrected via `ignore_changes` rather than blindly applying the drift.

![Play Convergence Check](./assets/play-convergence-no-changes.png)
*Figure 5 — Play convergence check — Run #35. Following the Terraform ownership and drift corrections, the Play environment produced a clean Terraform plan with no infrastructure changes. The reviewed saved plan was then applied successfully through the protected infrastructure gate without mutating Azure resources.*

### 6.4 Plan Retries & Image Deployment

![Infrastructure Apply Approval Gate](./assets/play-infra-apply-approval-gate.png)
*Figure 6 — Infrastructure Apply approval gate — after Terraform Plan completes, Run #32 pauses at `rg-xhuma-play-infra`. The immutable plan hash, target and expected container digest remain visible before the reviewed plan can be applied.*

![Play Infrastructure Apply Success](./assets/play-infra-apply-success.png)
*Figure 7 — Successful infrastructure Apply job, completing only after the strict plan review and GitHub Environment manual approval.*

1. **Plan Retries & Expiry**: If the apply step fails, it can be retried and will re-download the exact same plan blob securely. Plans expire automatically after 7 days in Blob Storage. If a plan is no longer valid, a completely new workflow run is required to generate and approve a new plan.
2. **Image Deployment**: After infrastructure applies the inert bootstrap image, the pipeline deploys the exact scanned Docker image digest. This step requires a separate environment approval.

![Play Deploy Digest](./assets/play-deploy-digest.png)
*Figure 8 — The application image is deployed deterministically using the exact immutable SHA256 digest validated during the build stage.*

### 6.5 Digest Rollback & Recovery
Deployment is deterministic. We record the previous digest before deploying and the new digest after.

1. **Recovery Ownership**: If a deployment introduces regressions, authorized operators can perform a rollback.
2. **Rollback Procedure**: 
   - Identify the previous known-good digest from the deployment step summary.
   - Manually trigger a recovery deployment via Azure CLI or a dedicated rollback workflow using that specific digest:
     ```bash
     az webapp config container set --name <app_service> --resource-group <rg> --docker-custom-image-name ghcr.io/...@sha256:...
     ```
   - Ensure older application code is compatible with the current Alembic database schema migrations. Immutable image rollback is available, but image rollback is only safe when the previous application release is compatible with the schema already migrated by the newer release. The formal migration compatibility / downgrade policy remains a handover-readiness item. Operators must not assume that reverting the container digest also reverts PostgreSQL.

---

## 7. Verification and Health Checks

### 7.1 Safe Verification Boundaries
- **Liveness Probe**: The `/health` endpoint is unauthenticated and returns a coarse HTTP 200 process-liveness signal. It does not leak secrets, tokens, or perform downstream NHS requests.
- **Protected Readiness**:
  - **Implemented liveness**: the pipeline calls `/health` after deployment.
  - **Implemented observability foundations**: Application Insights, Log Analytics and PostgreSQL diagnostics.
  - **Application safety**: startup configuration failures fail closed.
  - **Operational monitoring**: Xhuma uses Azure Monitor/Application Insights and Log Analytics for production telemetry. Alert rules also exist in the live Azure environment. The complete alert/routing configuration is not yet codified in the current Terraform deployment model; IaC reconciliation and routing standardisation are a planned operational-hardening activity.
- **Manual Clinical Check**: Because the GitHub Actions runner does not possess the required mTLS certificates, a manual synthetic test must be run from a trusted clinical workstation to verify SOAP mTLS and audit capabilities after deployment.

### 7.2 Operator Checklist for New Environments

**Implemented Readiness Checks (Automated):**
- [ ] Application liveness probe (HTTP 200).
- [ ] Status-only Key Vault resolution check (if supported by access model).
- [ ] Digest verification of the deployed container.

**Manual Prerequisites (To be done by Operator):**
- [ ] Target configuration defined in `infra/targets.json`.
- [ ] Scoped Azure SP access configured and credentials placed in GitHub.
- [ ] GitHub Environment approvals configured for infra apply and container deployment.
- [ ] Local Key Vault populated with `epic-ca-cert`.
- [ ] App Service integration subnet ID added to the shared configuration. *(Note: The shared Terraform root is fully operational. The matrix workflow automatically computes an ephemeral configuration to add the target App Service subnet to the shared Key Vault network ACL while preserving existing rules. A strict guard validates the plan to fail-closed if unexpected changes are proposed before applying.)*

**Follow-ups / Manual Exercises:**
- [ ] [TODO: automate] Trust-local authentication.
- [ ] [TODO: automate] Audit retention/access policies.
- [ ] PostgreSQL restore rehearsal and evidence (record recovery time/result and application recovery verification).
- [ ] Service-principal credential rotation rehearsal.
- [ ] Target Epic certificate rotation rehearsal (post-rotation deployment/mTLS verification).
- [ ] Run synthetic manual clinical check (SOAP/mTLS) from a trusted workstation.
- [ ] Rollback exercise performed and documented.

### 7.3 Custom Domain / DNS / TLS Onboarding

Assigning the externally agreed environment FQDN (e.g., `<environment-hostname>`) to the Azure App Service is a critical post-provisioning step. 

> **Current IaC Limitation:** Custom-domain DNS, App Service hostname binding, and managed-certificate onboarding are currently operator-managed post-provisioning steps. The current Terraform root does not manage the custom hostname binding or certificate. Therefore, a destructive App Service recreation can require the custom hostname/TLS binding to be manually re-established. Future IaC adoption is planned as an operational hardening item.

**Azure Portal Procedure:**
1. Navigate to your target App Service.
2. Select **Custom domains** > **Add custom domain**.
3. For the domain (e.g. `xhuma.co.uk`), use:
   - **Domain provider:** All other domain services
   - **TLS/SSL certificate:** App Service Managed Certificate
   - **TLS/SSL type:** SNI SSL
4. For a subdomain, document the normal DNS pattern exactly as instructed by Azure:
   - **CNAME:** `<environment hostname>` -> `<app-service-name>.azurewebsites.net`
   - **TXT:** `asuid.<environment hostname>` -> Azure Custom Domain Verification ID

*Note: Exact DNS record labels depend on whether the authoritative zone is `xhuma.co.uk` or a delegated child zone. Operators must use the exact records displayed by Azure. The TXT ownership-verification record should be retained permanently.*

**Validation:**
- DNS resolves correctly.
- Azure custom-domain validation passes.
- App Service hostname binding exists.
- App Service Managed Certificate reaches `Secured`.
- HTTPS works on the custom FQDN.
- `/health` returns HTTP 200 over the intended endpoint.

*(Optional CLI reference for hostname binding)*:
```bash
az webapp config hostname add \
  --webapp-name <app-name> \
  --resource-group <resource-group> \
  --hostname <fqdn>
```

### 7.4 ALLOWED_HOSTS Coupling

The application uses `TrustedHostMiddleware` via the `ALLOWED_HOSTS` configuration. 

For INT/PRD environments, when `ALLOWED_HOSTS` is tightened from `"*"`, it **must** include the external custom FQDN. 

> **Operator Check:** If the deployment pipeline continues to health-check the default Azure hostname (`.azurewebsites.net`), that default hostname must also remain permitted in `ALLOWED_HOSTS` until the CI/CD workflow is made custom-domain aware.

### 7.5 Epic / Relay Acceptance Sequence

Complete this sequence to achieve external environment functional acceptance:
1. Agree/register the external FQDN.
2. Create the necessary DNS records.
3. Bind the custom hostname to the App Service.
4. Provision and verify the TLS certificate.
5. Populate the target-local `epic-ca-cert`.
6. Refresh the App Service Key Vault references.
7. Verify that `EPIC_CA_CERT` = `Resolved`.
8. Configure the relay/Epic-facing endpoint to use the agreed custom FQDN.
9. Verify `/health` over the custom FQDN.
10. Perform a SOAP/mTLS functional request.
11. Record evidence.

*Make clear that `"Using HSCN Relay"` in startup logs is configuration evidence only and does not prove relay connectivity.*

*Note: Complete clinical end-to-end acceptance requires all downstream services (like GP Connect) to be operational. GP Connect functional acceptance may be temporarily blocked by an external downstream outage, even if infrastructure, PDS, and Epic TLS acceptance remain fully valid. Do not overstate production readiness until all endpoints succeed.*

### 7.6 Key Vault Reference Verification

A mandatory verification step must be performed post-Terraform and pre-functional-testing to ensure the App Service can resolve its `@Microsoft.KeyVault(...)` configuration references. Note that Terraform automatically provisions the App Service managed-identity access policy on the shared Key Vault.

When the shared Key Vault is configured with `DefaultAction = Deny`, the target App Service integration subnet must explicitly be permitted by the vault's network ACL.

**Verification Sequence:**
1. Confirm the App Service system-assigned managed identity exists.
2. Confirm the required secret permissions/access policy exist on the shared vault.
3. Confirm the target App Service subnet is permitted by the shared vault's network ACL.
4. Confirm the App Service Key Vault reference status is `Resolved`.
5. **Do not proceed** to functional testing if the reference status is `AccessToKeyVaultDenied`, `SecretNotFound`, or any other unresolved state.

*(Note: Verification can be performed via the Azure Portal or using the `az webapp config appsettings` command to verify values are appropriately retrieved rather than remaining as raw references).*

*Note: Forcing an App Service Key Vault reference refresh can return a transient HTTP 409 Conflict if another operation is active. Bounded retries should be used. Furthermore, a `Resolved` status does not prove that the application has picked up the *latest* content (only that it can successfully resolve *a* version). Positive application-level trust evidence must be verified after certificate rotation.*

### 7.7 Post-Deployment Health Verification

Run the following reproducible command to verify application liveness:

```bash
curl -sS \
  -o /tmp/xhuma-health.json \
  -w 'HTTP %{http_code}\n' \
  https://<app_service>.azurewebsites.net/health

cat /tmp/xhuma-health.json
```

![Final Health State](./assets/final-health-state.png)
*Figure 9 — Post-deployment liveness verification: the Play App Service returned HTTP 200 with `{"status":"ok"}`. This is a coarse liveness signal, and does not replace deep clinical/readiness testing.*

### 7.8 GP Connect Commissioning Verification and Domain Readiness

Production commissioning on 14 October 2026 confirmed that application process liveness (`/health`), Key Vault reference resolution, and external TLS handshake success are necessary prerequisites, but do not by themselves establish end-to-end clinical connectivity or standards readiness.

#### 7.8.1 GP Connect Commissioning Diagnosis Checklist

For a healthy, uncached transaction, the operator must verify each discrete link in the end-to-end routing and processing chain:

1. **SOAP request received:** Inbound ITI-38 query reaches `/SOAP/DocumentRegistry_Service` over mTLS (`AppRequests`).
2. **Fresh/correct PDS result:** Upstream PDS resolves the patient record and general practitioner identifier (`pds_lookup`).
3. **Expected GP ODS:** The extracted GP practice ODS matches the expected practice under test.
4. **SDS Device resolution:** Upstream Spine Directory Service query returns a FHIR Device Bundle matching the GP practice ODS (`sds_trace`).
5. **ASID + MHS Party Key resolution:** Parser extracts valid Spine ASID and `nhsMhsPartyKey` identifiers from the Device resource.
6. **SDS Endpoint resolution:** Secondary SDS query resolves the supplier's FHIR endpoint URL for `urn:nhs:names:services:gpconnect:fhir:operation:gpc.getstructuredrecord-1` (`fhir_endpoint_trace`).
7. **Downstream GP Connect request:** Outbound request is dispatched to the supplier via the designated route (HSCN Relay or direct internet path, recorded in `AppDependencies`).
8. **Supplier response:** Upstream GP system returns HTTP 200 with the requested FHIR Parameters payload.
9. **Document conversion and retrieval:** Xhuma converts FHIR resources into a valid CDA/CCD document, caches the document in Redis, and assigns a document identifier.
10. **Corresponding PostgreSQL audit trail:** All pipeline stages persist structured audit events (`audit_event`) in strict chronological sequence.

> [!IMPORTANT]
> **Readiness Assertion Boundary:** Successful infrastructure health, PDS connectivity, or SDS connectivity alone does **not** establish end-to-end GP Connect readiness. Operators must verify that a request was successfully fulfilled by the supplier and audited before certifying an environment.

#### 7.8.2 Clinical-Domain Configuration and DAPB4013 Scope

GP Connect query domain inclusion is configuration-driven via target environment variables (`GP_CONNECT_INCLUDE_*`):
- `GP_CONNECT_INCLUDE_MEDICATION`: Controls inclusion of medication summary and issues.
- `GP_CONNECT_INCLUDE_ALLERGIES`: Controls inclusion of allergy and adverse reaction records.
- `GP_CONNECT_INCLUDE_PROBLEMS`: Controls inclusion of active and resolved problem lists.
- `GP_CONNECT_INCLUDE_INVESTIGATIONS`: Controls inclusion of investigation and test results.
- `GP_CONNECT_INCLUDE_IMMUNISATIONS`: Controls inclusion of immunisation records.

Operators must confirm that the intended clinical domains are enabled for the specific commissioned use case. For example, in the production configuration baseline (`prd.tfvars`), `GP_CONNECT_INCLUDE_MEDICATION` is enabled (`"true"`), whereas `GP_CONNECT_INCLUDE_ALLERGIES` is disabled (`"false"`).

> [!NOTE]
> **DAPB4013 Assurance Scope:** Enabling medicines alone does not establish that the complete medicines-and-allergies admission use case has been commissioned. Formal DAPB4013 conformance and clinical safety assurance belong in the dedicated assurance documentation and clinical safety case; standards compliance must never be inferred merely from successful technical connectivity or HTTP 200 responses.

#### 7.8.3 Test-Patient Registration and Upstream Data Caveats

When conducting commissioning tests with synthetic or test patients, operators must take account of upstream registration integrity:
- Synthetic patients may have intentionally or accidentally inconsistent GP registrations between the supplier system (e.g. EMIS, TPP SystmOne) and NHS PDS.
- Xhuma deterministically routes according to the GP ODS code returned by the PDS patient lookup. If PDS returns an ODS code different from the supplier's expected test practice, Xhuma will query SDS for the PDS-registered practice. Consequently, the supplier will observe zero incoming traffic.
- Operators must obtain positive confirmation that the test patient's PDS GP registration matches the intended supplier practice before diagnosing the absence of supplier traffic as a relay, network, or GP Connect defect.

---

## 8. Shared Infrastructure Adoption (Terraform)

The shared Key Vault is now adopted into a separate shared Terraform state. The matrix workflow automatically:
- reads the current network ACL;
- preserves existing rules;
- adds only the current target app subnet;
- guards the shared plan to fail-closed if unexpected changes are proposed;
- verifies the target subnet is successfully added post-apply.

Run #36 demonstrated automatic restoration of this network link after the Play subnet was deliberately destroyed.

When managing shared infrastructure components (such as the shared Key Vault network ACLs) in Terraform:
- Ensure the resource includes a `lifecycle { prevent_destroy = true }` block.
- **Note:** `prevent_destroy` does not guarantee Terraform will never *propose* replacement. Instead, any proposed destruction or replacement of the imported shared Key Vault will be blocked by `prevent_destroy` during the apply phase.

---

## 9. Quick Operator Commands

Use these safe generic commands to inspect environments. Replace placeholders (e.g. `<target-subscription-id>`, `<app_service>`, `<rg>`) with the actual environment values.

- **Current Azure subscription:**
  ```bash
  az account show --query name -o tsv
  ```
- **App Service managed identity:**
  ```bash
  az webapp identity show --name <app_service> --resource-group <rg> --query principalId -o tsv
  ```
- **SHA256 verification:**
  ```bash
  sha256sum <target>-plan.tfplan
  ```
- **Changed Terraform resources/actions:**
  ```bash
  terraform show -json <target>-plan.tfplan | jq '.resource_changes[] | {address, actions: .change.actions}'
  ```
- **Changed Terraform attribute paths:**
  ```bash
  terraform show -json <target>-plan.tfplan | jq -r '
    .resource_changes[]
    | select(.change.actions != ["no-op"])
    | .address as $addr
    | (.change.before // {}) as $before
    | (.change.after // {}) as $after
    | ([($before | paths(scalars)), ($after | paths(scalars))] | unique[]) as $p
    | select(($before | getpath($p)) != ($after | getpath($p)))
    | "\($addr)\t\($p | map(tostring) | join("."))"
  '
  ```
- **App Service health:**
  ```bash
  curl -s https://<app_service>.azurewebsites.net/health
  ```
- **Key Vault reference verification (list settings):**
  ```bash
  az webapp config appsettings list --name <app_service> --resource-group <rg> --query "[?contains(value, '@Microsoft.KeyVault')].{name:name, value:value}" -o table
  ```
- **Currently deployed container digest:**
  ```bash
  az webapp config show --name <app_service> --resource-group <rg> --query 'linuxFxVersion' -o tsv
  ```
- **Relevant workflow/run identifiers:**
  ```bash
  gh run view <run-id>
  ```

### Azure Runtime Diagnostics and Audit Verification

Operational monitoring and triage across Azure App Service, Application Insights (Log Analytics), and private PostgreSQL must adhere to strict security guardrails:
- Commands and queries are read-only unless explicitly stated otherwise.
- Never print or export secret-valued application settings or Key Vault secret contents.
- Never print raw audit `detail`, `subject_ref`, patient identifiers, access tokens, SOAP payloads, or downstream FHIR response bodies into public channels or ticket trackers.
- Prefer metadata, presence, and count checks over data dumping.
- Never enable SSH, public PostgreSQL access, or temporary firewall exceptions merely for routine inspection.

#### Live application logs

```bash
az webapp log tail \
  --name <app-service> \
  --resource-group <resource-group>
```

> [!WARNING]
> Operators must avoid capturing or publishing protected health information (PHI), bearer tokens, or credentials when streaming application logs.

#### PDS to SDS to GP Connect Troubleshooting Flow

When diagnosing an ITI-38 (`DocumentRegistry_Service`) transaction, the complete end-to-end routing chain executes as follows:

```
ITI-38 SOAP Request
  │
  ▼
PDS Patient Lookup (resolves patient record & generalPractitioner ODS)
  │
  ▼
Extract generalPractitioner ODS
  │
  ▼
SDS Device Lookup (queries Spine Directory Service for GP ODS)
  │
  ▼
Extract ASID & MHS Party Key (from Device identifier list)
  │
  ▼
SDS Endpoint Lookup (queries Spine Directory Service for endpoint URL)
  │
  ▼
GP Connect Request (dispatched via HSCN Relay or direct internet route)
```

**Isolating Failure Boundaries:**
If a GP system supplier reports that no request reached their practice system, operators must determine how far through this routing chain the transaction actually progressed before assuming a relay, network, or supplier outage:
1. Did PDS return a valid patient record and practice ODS?
2. Did SDS return a valid Device Bundle containing matching entries for that ODS?
3. Were the ASID and MHS Party Key successfully parsed?
4. Did SDS resolve the FHIR endpoint URL?
5. Did Xhuma actually dispatch the downstream GP Connect request (creating an `AppDependencies` record)?

If the chain stopped before the downstream GP Connect call, the supplier will naturally see zero incoming traffic. In that scenario, the root cause lies within upstream resolution (e.g. missing SDS Device registration or mismatched PDS GP practice), not downstream supplier or relay connectivity.

> [!CAUTION]
> **SOAP HTTP 200 False Assurance Caveat:**
> An HTTP 200 response on `/SOAP/DocumentRegistry_Service` does **not** by itself prove clinical or XDS query success.
> When downstream GP Connect fails or SDS resolution fails, Xhuma catches the failure, logs `gpconnect failed`, audits `iti38_document_query` as `fail` with `error_code="GP_CONNECT_FAILURE"`, and returns an XDS registry error (`RegistryErrorList` with status `Failure` and `XDSRegistryError`) enclosed within an HTTP 200 SOAP response envelope. Operators must never rely on `AppRequests | where ResultCode == 200` as proof of successful clinical data retrieval.

#### Cache-Aware Diagnosis and Selective Invalidation

Xhuma implements multi-tier caching in Redis. Diagnosing routing discrepancies requires understanding cache boundaries:

- **PDS Patient Caching:**
  - Patient records returned from PDS are cached in Redis using deterministic pseudonymous keys: `pds:patient:<sha256_digest>`.
  - The digest is computed as an HMAC-SHA256 of the NHS number using `PDS_CACHE_HMAC_SECRET` (or `API_KEY`). Raw NHS numbers are never used as Redis keys.
- **SDS Lookups Caching:**
  - SDS Device and Endpoint lookups are cached separately from patient data under deterministic keys:
    - Device queries: `pds:sds:<env>:device:<ODS>`
    - Endpoint queries: `pds:sds:<env>:endpoint:<ODS>:<partykey>`
- **Cache Hit vs Fresh Response:**
  - A cache hit (`"Cache hit for PDS patient query"` or `"Cache hit for SDS query..."`) satisfies the request immediately from Redis. The upstream national NHS PDS or SDS API is **not** consulted during that transaction.
  - Before escalating apparent routing discrepancies to suppliers or NHS England, verify whether the transaction operated on cached data or a fresh response.
- **Selective Cache Invalidation Procedure:**
  - If a test patient's registration has changed upstream (e.g. registered at a new GP practice in PDS or test environment), invalidate *only* the specific affected cache entries through an approved operational path (e.g. deleting the specific SDS device key: `DEL pds:sds:<env>:device:<ODS>`, or the patient's HMAC key if known), and repeat the test.
- **Operational Rules:**
  - **Never flush production Redis:** Never execute `FLUSHALL` or `FLUSHDB` across the production Redis instance simply to retest one patient. Flushing drops all active sessions, token caches, and CCDA document caches across the entire trust environment.
  - **Never publish raw identifiers:** Never publish raw NHS numbers, cache secrets, tokens, or cached Patient JSON payloads as operational evidence in incident tickets or Git commits.

#### SDS Bundle Resolution and Current Error Semantics

During PRD commissioning on 14 October 2026, the diagnostic behaviour of an empty SDS Bundle response was confirmed against the live application:

- **Current Implementation Behaviour (`app/gpconnect.py`):**
  When SDS returns a valid FHIR Bundle response that contains no matching Device for the practice ODS (`"total": 0`, `"entry": []`), the parsing logic attempts:
  ```python
  asid_trace.get("entry", [{}])[0].get("resource", {}).get("identifier", [])
  ```
  Because `"entry"` exists as an empty list (`[]`), `asid_trace.get("entry", [{}])` evaluates to `[]`. Indexing `[0]` raises an unhandled `IndexError: list index out of range`. This is caught by the enclosing block and logged as:
  ```
  Unable to parse SDS trace response: list index out of range
  ```
  The endpoint then returns an HTTP 500 JSONResponse (`{"success": False, "error": "Unable to parse SDS trace response: list index out of range"}`).

- **Operator Interpretation:**
  This error indicates that the SDS API was reached successfully over the network and returned a valid HTTP 200 response, but the directory contained no usable matching Device resource for the practice ODS queried. It is **not** evidence of a network, firewall, or relay failure between Xhuma and SDS.

- **Hardening Item Status:**
  This is an operator troubleshooting note to assist triage. It is a known hardening item (replacing vulnerable `[0]` indexing with defensive `entry` length checks) rather than intended steady-state application behaviour.

#### Audit Trail Interpretation Caveats

Operators verifying audit events in PostgreSQL or Log Analytics must be aware of current audit event ordering in `app/gpconnect.py`:

- **Premature `sds_trace = ok` Persistence:**
  In the current implementation, `sds_trace` is audited with `outcome = AuditOutcome.ok` immediately after the `sds_trace()` network call returns, *before* the returned Bundle is parsed for a usable ASID or MHS Party Key.
- **Resulting Audit Sequence:**
  When SDS returns an empty Device Bundle and parsing subsequently fails with `IndexError`, the audit sequence in PostgreSQL legitimately appears as:
  ```
  action                outcome  error_code
  --------------------  -------  --------------------
  pds_lookup            ok       NULL
  sds_trace             ok       NULL
  iti38_document_query  fail     GP_CONNECT_FAILURE
  ```
  The downstream action `resolve_asid_partykey` is never reached because the handler exits early on the parsing error.
- **Interpretation Caveat for Operators:**
  Until the application is hardened, an audit record of `sds_trace = ok` proves only that the SDS query completed successfully over the network; it does **not** prove successful Device, ASID, or Party Key resolution. Operators must not draw a false assurance conclusion that SDS routing succeeded merely because `sds_trace = ok` is recorded.

#### Log Analytics Telemetry Queries

Use the following read-only Kusto Query Language (KQL) queries in Azure Monitor / Log Analytics to diagnose transactions without exposing clinical payloads.

##### 1. Resolve Log Analytics Workspace ID

```bash
LAW_ID=$(az monitor log-analytics workspace show \
  --resource-group <resource-group> \
  --name <workspace-name> \
  --query customerId \
  -o tsv)
```

##### 2. Recent Inbound SOAP Requests

```bash
az monitor log-analytics query \
  --workspace "$LAW_ID" \
  --analytics-query '
AppRequests
| where TimeGenerated > ago(60m)
| where Url contains "/SOAP/"
| project TimeGenerated, Name, Url, ResultCode, Success, DurationMs, OperationId
| order by TimeGenerated desc
' \
  -o table
```

*(Note: As documented above, `ResultCode == 200` confirms HTTP transport delivery, not clinical XDS query success).*

##### 3. Recent PDS and SDS Cache Hit/Miss Events

```bash
az monitor log-analytics query \
  --workspace "$LAW_ID" \
  --analytics-query '
AppTraces
| where TimeGenerated > ago(60m)
| where Message has_any ("Cache hit for PDS", "Cache miss for PDS", "Cache hit for SDS", "Cache miss for SDS")
| project TimeGenerated, Message, SeverityLevel, OperationId
| order by TimeGenerated desc
' \
  -o table
```

##### 4. Extract Practice ODS Code from SDS Cache Traces

SDS Device cache keys follow the pattern `pds:sds:<env>:device:<ODS>`. Operators can extract the queried ODS code directly from trace logs:

```bash
az monitor log-analytics query \
  --workspace "$LAW_ID" \
  --analytics-query '
AppTraces
| where TimeGenerated > ago(60m)
| where Message has "pds:sds:"
| extend OdsCode = extract(@"pds:sds:[^:]+:device:([A-Za-z0-9]+)", 1, Message)
| where isnotempty(OdsCode)
| project TimeGenerated, OdsCode, Message, OperationId
| order by TimeGenerated desc
' \
  -o table
```

##### 5. Correlate a Single Transaction by OperationId Across Telemetry Tables

To follow a complete transaction across requests, dependencies, traces, and exceptions using its `OperationId`:

```bash
az monitor log-analytics query \
  --workspace "$LAW_ID" \
  --analytics-query '
let opId = "<OPERATION_ID>";
union
    (AppRequests | where OperationId == opId | project TimeGenerated, Table="AppRequests", Name=Url, Success, ResultCode, Detail=strcat("Duration: ", tostring(DurationMs), "ms")),
    (AppDependencies | where OperationId == opId | project TimeGenerated, Table="AppDependencies", Name, Success, ResultCode, Detail=tostring(Properties)),
    (AppTraces | where OperationId == opId | project TimeGenerated, Table="AppTraces", Name=tostring(SeverityLevel), Success=bool(null), ResultCode="", Detail=Message),
    (AppExceptions | where OperationId == opId | project TimeGenerated, Table="AppExceptions", Name=OuterType, Success=false, ResultCode="", Detail=OuterMessage)
| order by TimeGenerated asc
' \
  -o table
```

Or query individual tables for targeted investigation:

```bash
# 1. AppDependencies: inspect external dependencies and instrumented pipeline stages
AppDependencies
| where OperationId == "<OPERATION_ID>"
| project TimeGenerated, Name, Type, Target, Success, ResultCode, DurationMs, Properties
| order by TimeGenerated asc

# 2. AppTraces: inspect non-PHI diagnostic log lines
AppTraces
| where OperationId == "<OPERATION_ID>"
| project TimeGenerated, Message, SeverityLevel
| order by TimeGenerated asc

# 3. AppExceptions: inspect recorded application failures
AppExceptions
| where OperationId == "<OPERATION_ID>"
| project TimeGenerated, OuterType, OuterMessage, ProblemId
```

##### 6. Identify GP Connect Failures and Empty SDS Errors

```bash
az monitor log-analytics query \
  --workspace "$LAW_ID" \
  --analytics-query '
AppTraces
| where TimeGenerated > ago(60m)
| where Message has "gpconnect failed" or Message has "Unable to parse SDS trace"
| project TimeGenerated, Message, SeverityLevel, OperationId
| order by TimeGenerated desc
' \
  -o table
```

Check recorded exceptions in `AppExceptions`:

```bash
az monitor log-analytics query \
  --workspace "$LAW_ID" \
  --analytics-query '
AppExceptions
| where TimeGenerated > ago(60m)
| where OuterMessage has "list index out of range" or OuterMessage has "SDS trace"
| project TimeGenerated, OuterType, OuterMessage, ProblemId, OperationId
| order by TimeGenerated desc
' \
  -o table
```

##### 7. Check Downstream GP Connect Dependency Creation

To verify whether Xhuma ever initiated an outbound call to the GP Connect supplier or relay:

```bash
az monitor log-analytics query \
  --workspace "$LAW_ID" \
  --analytics-query '
AppDependencies
| where OperationId == "<OPERATION_ID>"
| where Name == "xhuma.gpconnect.http" or Target has "Patient/$gpc.getstructuredrecord" or Target has "proxy.national.ncrs.nhs.uk"
| project TimeGenerated, Name, Target, Success, ResultCode, DurationMs
' \
  -o table
```

> [!TIP]
> If SDS Device resolution fails or PDS lookup fails, `xhuma.gpconnect.http` is never dispatched. If this query returns zero rows for an `<OPERATION_ID>`, the transaction failed prior to downstream dispatch.

##### 8. Audit Persistence Telemetry

```bash
az monitor log-analytics query \
  --workspace "$LAW_ID" \
  --analytics-query '
AppDependencies
| where TimeGenerated > ago(60m)
| where Name == "xhuma.audit.persist"
| project TimeGenerated, Success, ResultCode, DurationMs, OperationId, Properties
| order by TimeGenerated desc
' \
  -o table
```

`Success=True`, `ResultCode=0`, and `stage.outcome=ok` demonstrate successful completion of the instrumented audit-persistence operation. They do not by themselves validate every stored row field.

##### 9. Pipeline Stage Duration and Outcome

```bash
az monitor log-analytics query \
  --workspace "$LAW_ID" \
  --analytics-query '
AppDependencies
| where TimeGenerated > ago(60m)
| where Name startswith "xhuma."
| project TimeGenerated, Name, Success, DurationMs, OperationId, Properties
| order by TimeGenerated desc
' \
  -o table
```

#### Private PostgreSQL Audit Verification

PostgreSQL Flexible Server in Xhuma is deployed private-only (`public_network_access_enabled = "Disabled"`) inside an isolated Azure Virtual Network. Operators must never temporarily enable public access or add firewall exceptions merely to inspect audit records.

##### Approved Diagnostic Container Pattern

To verify audit persistence directly against the database during commissioning or incident triage:

1. **Authentication:** Authenticate using the existing scoped Azure deployment or administration identity (`az login` with appropriate RBAC).
2. **Diagnostic Container:** Run a short-lived diagnostic container within an already-approved VNet/subnet capable of reaching the private PostgreSQL Flexible Server (e.g. an Azure Container Instance or App Service diagnostic task attached to the database integration subnet).
3. **Client Image:** Use a standard, trusted PostgreSQL client image (e.g. `postgres:15-alpine`).
4. **Read-Only Queries:** Execute `SELECT`-only queries. Never execute write or mutation commands during diagnosis.
5. **Correlation by Trace ID:** Query records by `trace_id` or `OperationId` rather than patient identifier. Never search or filter by raw NHS numbers.
6. **Scoped Field Retrieval:** Retrieve only the specific columns needed for diagnosis.
7. **Immediate Teardown:** Terminate and destroy the temporary container immediately upon completion of the verification session.

##### Generic Audit Verification SQL Query

```sql
SELECT
    event_time,
    sequence,
    action,
    outcome,
    error_code,
    request_id,
    trace_id,
    detail
FROM audit_event
WHERE trace_id = '<TRACE_ID>'
ORDER BY sequence ASC;
```

> [!WARNING]
> **Data Protection Rule:** Do not document real database credentials, service-principal credentials, subscription IDs, patient identifiers, or actual production trace IDs in public runbooks or incident tickets.

---

## 10. Assurance and Evidence Records

Specific deployment rehearsals and assurance events are captured as immutable evidence records. These records are retained separately from this living operational runbook to preserve point-in-time factual observations.

- [Play Matrix Deployment Rehearsal (23 September 2026)](./assurance/evidence/2026-09-23-play-matrix-deployment-rehearsal.md)
- [INT Matrix Cutover (6 October 2026)](./assurance/evidence/2026-10-06-int-matrix-cutover.md)


---

