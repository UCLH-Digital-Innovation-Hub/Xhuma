# PRD Terraform State Reconciliation — 9 October 2026

## Context

During deployment of PR #257, the PRD migration guard stopped the Terraform plan because it detected destructive replacement actions for:

- `azurerm_key_vault_access_policy.app_shared_policy`
- `azurerm_key_vault_access_policy.locust_shared_policy`

The deployment stopped at plan stage. No Terraform apply or application deployment occurred.

## Finding

The PRD Terraform state incorrectly associated both shared Key Vault access-policy resources with:

`xhuma-shared-kv-int`

The intended production shared Key Vault is:

`xhuma-shared-kv-prd`

The managed identities recorded in state were confirmed as:

- App Service MI: `b11aa287-ae13-4e3b-a32b-ae912933b48b`
- Locust MI: `8241d9f4-1f36-4325-b4a7-f64ccde99af6`

The App Service managed identity already had an access policy on the PRD shared Key Vault. The Locust managed identity did not.

## Reconciliation

The stale shared-vault access-policy entries were removed from PRD Terraform state only. No Azure resources were deleted.

The existing PRD App Service access policy was then imported into:

`azurerm_key_vault_access_policy.app_shared_policy`

Verification confirmed that this state resource now references:

`xhuma-shared-kv-prd`

The Locust shared policy remains absent from Terraform state so that Terraform can create the missing PRD policy normally on the next deployment plan.

## Safety outcome

The migration guard behaved as designed by preventing Terraform from applying a plan containing destructive access-policy replacements.

A fresh deployment plan is required after this state reconciliation. Acceptance criteria remain:

- zero destructive deletes;
- zero resource replacements;
- only expected PRD configuration changes;
- creation of the missing PRD Locust shared-vault access policy is acceptable.
