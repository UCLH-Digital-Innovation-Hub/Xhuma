# 2026-09-25: Play Clean-Room Rebuild and Convergence Evidence

## 1. Scope
This evidence document records the destructive teardown of Terraform-managed Play infrastructure and its subsequent clean-room rebuild from an empty target Terraform state. It documents the automatic reconciliation of the shared Key Vault network ACL, application deployment, and a subsequent no-change convergence run.

The Play resource group and Terraform backend state storage were deliberately retained outside the target infrastructure lifecycle.

## 2. Clean-room teardown baseline
The reviewed destroy plan resulted in:
- 0 added
- 0 changed
- 29 destroyed

After destruction, the following baseline was observed:
- The target Terraform state was empty.
- Play VNet/subnets were absent.
- Play target resources were absent.
- Play Terraform backend/state storage survived.
- The shared Key Vault survived.
- Azure automatically removed the deleted Play subnet from the shared Key Vault network ACL.

This last point exposed a real lifecycle dependency that was subsequently addressed in the deployment workflow.

## 3. Clean-room rebuild — Run #36
**GitHub Actions:** Matrix Deployment Pilot  
**Run ID:** 36131210090  
**Source commit:** `ae1024d65d0b9d1aca70fce2a66450e2c8602a6d`  

**Terraform plan:**
- **Plan hash:** `bd86b99c291f8331469b0e460c9e957b1a4f7e08605f9e2afc9e995dc1aaa20a`
- **Candidate image:** `sha256:4748cd395214178755362ca8d9e571830bf6007b9ede81312f46b92b29514672`
- **Plan result:** 29 to add, 0 to change, 0 to destroy.

The following stages succeeded:
- Terraform Apply
- Shared Infrastructure Plan
- Shared Terraform plan guard
- Shared Infrastructure Apply
- Shared Key Vault configuration verification

The workflow executed the following logic:
- Recreated the target app subnet.
- Read the existing shared Key Vault ACL.
- Preserved existing ACL entries.
- Proposed only the addition of the Play app subnet.
- Passed the fail-closed shared plan guard.
- Applied the shared Terraform plan.
- Verified the Play subnet was present afterwards.

**No manual Azure infrastructure repair was performed between the clean-room teardown and successful shared Key Vault reconciliation.**

## 4. Run #36 transient App Service refresh race
Run #36 subsequently stopped at the `Refresh and Verify App Service Key Vault References` step because Azure returned:

`HTTP 409 Conflict`  
`ExtendedCode: 04139`  
`"Cannot refresh keyvault references for this site because another operation is in progress."`

This occurred after the target infrastructure and shared Key Vault reconciliation had already succeeded. It was an App Service control-plane concurrency condition rather than Terraform drift or infrastructure failure. No manual Azure repair was undertaken. The workflow was hardened with bounded retry/backoff logic for this specific transient conflict.

**Follow-up commit:** `1a17c9cf22cdab442841c429416bb5f7b15c1838`

Run #36 itself is not described as a completely successful end-to-end deployment because the application deployment job did not execute after the failed refresh step.

## 5. Convergence — Run #37
**GitHub Actions:** Matrix Deployment Pilot  
**Run ID:** 36139318165  
**Source commit:** `1a17c9cf22cdab442841c429416bb5f7b15c1838`  

**Terraform Plan Summary:**
- **Plan hash:** `ef8f5efbfebd5337f107b4f70575819206f10571b873431de5c329bfe6bcb45e`
- **Expected image digest:** `sha256:301cb6c8289af01cd0146cb13292cc3df490c4b99fcf8a766f74e61bf7556cf4`
- **Terraform result:** `No changes. Your infrastructure matches the configuration.`

Run #37 successfully completed:
- CI & Tests
- Build & Push
- Trivy vulnerability gate
- Target validation
- Terraform Plan
- Manifest/plan integrity checks
- Terraform Apply
- Shared Infrastructure Plan
- Shared plan guard
- Shared Infrastructure Apply
- Shared Key Vault subnet verification
- App Service Key Vault reference refresh
- Shared Key Vault reference status verification
- Application deployment
- Immutable digest verification
- Application health check

This provides convergence evidence: after the clean-room rebuild, a subsequent deployment observed no target infrastructure drift.

## 6. Runtime evidence
Observed application startup evidence from 25 September 2026:
- Database migrations executed successfully.
- PostgreSQL transactional DDL detected.
- Migrations included audit tables, sequence changes, timezone-aware datetime and nullable `subject_ref`.
- Uvicorn started successfully.
- Application startup completed.
- Application Insights OpenTelemetry instrumentation enabled.
- Application reported `"Using HSCN Relay"`.
- GitHub Actions application health check succeeded.

Additionally, an unauthenticated request to `/robots933456.txt` was rejected with HTTP 403 because `X-ARR-ClientCert` was absent. This is interpreted narrowly as evidence that the application mTLS middleware rejected an unauthenticated request. 

This does **not** prove HSCN relay connectivity or complete SOAP/mTLS functional interoperability.

## 7. Security / deployment controls evidenced
Observed evidence indicates that the following deployment controls operated as intended:
- Target-isolated Terraform state
- Retained backend outside target lifecycle
- Saved Terraform plans
- SHA256 plan integrity
- Plan metadata binding
- Immutable container image digest
- Trivy HIGH/CRITICAL gate
- Protected apply environment
- Target/shared Terraform state separation
- Shared Key Vault prevent-destroy protection
- Add-only target subnet reconciliation
- Preservation of existing shared KV network rules
- Fail-closed shared plan guard
- Post-apply shared ACL verification
- Explicit App Service Key Vault reference refresh
- Shared reference resolution verification
- Bounded retry for transient Azure App Service 409 conflicts
- Health verification

Absolute security is not claimed.

## 8. Remaining functional acceptance
The following are explicitly marked as OUTSTANDING at the time of drafting:
- Agree and register environment external FQDN.
- Create required DNS records.
- Complete Azure App Service custom hostname binding.
- Provision and verify App Service Managed Certificate (TLS).
- Populate Play-local `epic-ca-cert`.
- Refresh App Service Key Vault references.
- Verify `EPIC_CA_CERT` reports `Resolved`.
- Configure/confirm relay endpoint as required.
- Perform SOAP/mTLS functional request.
- Demonstrate end-to-end HSCN/Epic connectivity appropriate to the Play environment.

The startup log `"Using HSCN Relay"` is configuration/startup evidence only, not proof that connectivity has been successfully exercised.

## 9. Conclusion
The Play rehearsal provides observed evidence that Xhuma target infrastructure can be destroyed and reconstructed through the controlled deployment pipeline without manual Azure infrastructure repair, including automatic restoration of the target's required shared Key Vault network relationship.

The subsequent deployment converged with no Terraform target changes and successfully completed shared-infrastructure verification, immutable application deployment, and automated health verification.

Production readiness is not asserted by this evidence. Environment-specific credentials, certificates, connectivity, functional interoperability and production assurance remain separately required.

## 10. Evidence table

| Phase | Description | Reference / Identity |
|-------|-------------|----------------------|
| Clean-room destroy | Verified destruction of Play infrastructure (0 add, 0 change, 29 destroy) | `2026-09-23-play-matrix-deployment-rehearsal.md` |
| Clean-room create plan | Recreation plan for 29 target resources | Plan hash: `bd86b99c...` (Run #36) |
| Target apply | Successful target infrastructure build | Commit: `ae1024d...` (Run #36) |
| Shared KV reconciliation | Add-only target subnet ACL applied successfully | Run ID: `36131210090` |
| Transient refresh race | 409 Conflict observed on App Service KV refresh | ExtendedCode: `04139` |
| Retry hardening | Implementation of bounded backoff for HTTP 409 | Commit: `1a17c9c...` |
| Convergence plan | Infrastructure reported matching configuration (0/0/0) | Plan hash: `ef8f5ef...` (Run #37) |
| Digest verification | Correct immutable digest pulled and run | Digest: `sha256:301cb6c...` |
| Health verification | Automated CI check reported healthy endpoint | Run ID: `36139318165` |
| Application startup | Database migrations, Uvicorn, and OpenTelemetry initialized | App Insights startup logs |
| mTLS rejection | Middleware rejected missing `X-ARR-ClientCert` | HTTP 403 on `/robots933456.txt` |
| Epic functional test | **OUTSTANDING**: Interoperability test for HSCN/Epic | N/A |

*For prior run contexts, see [2026-09-23-play-matrix-deployment-rehearsal.md](./2026-09-23-play-matrix-deployment-rehearsal.md).*
