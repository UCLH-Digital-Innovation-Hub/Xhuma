# This separate root owns the shared vault and its firewall in shared state.
# After workload apply, deploy-target.yml reconciles the target app subnet here;
# workload states continue to own their managed-identity access policies.
terraform {
  # The workflow selects backends/shared.hcl or backends/prd.hcl at init time.
  backend "azurerm" {}
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.0"
    }
  }
}

# Shared reconciliation supplies the vault subscription through ARM_SUBSCRIPTION_ID.
provider "azurerm" {
  features {}
}

# Fall back to the authenticated tenant when tenant_id is not supplied.
data "azurerm_client_config" "current" {}

resource "azurerm_key_vault" "shared_kv" {
  name                            = var.shared_key_vault_name
  location                        = var.location
  resource_group_name             = var.shared_resource_group_name
  tenant_id                       = var.tenant_id != "" ? var.tenant_id : data.azurerm_client_config.current.tenant_id
  sku_name                        = var.sku_name
  enabled_for_disk_encryption     = var.enabled_for_disk_encryption
  enabled_for_deployment          = var.enabled_for_deployment
  enabled_for_template_deployment = var.enabled_for_template_deployment
  soft_delete_retention_days      = var.soft_delete_retention_days
  purge_protection_enabled        = var.purge_protection_enabled
  # Per-target azurerm_key_vault_access_policy resources grant secret access.
  enable_rbac_authorization       = false
  public_network_access_enabled   = var.public_network_access_enabled

  lifecycle {
    # Block destruction or replacement while this resource remains configured.
    prevent_destroy = true
    ignore_changes = [
      # Policies belong to workload states; contacts and CostCenter are external.
      access_policy,
      contact,
      tags["CostCenter"]
    ]
  }

  # The workflow preserves live IP/subnet rules and adds the target app subnet.
  # guard_shared_plan.py permits only that subnet addition (or no changes).
  # Firewall admission is separate from the identity's secret permissions.
  network_acls {
    default_action             = "Deny"
    bypass                     = "AzureServices"
    ip_rules                   = var.approved_ip_rules
    virtual_network_subnet_ids = var.approved_subnet_ids
  }
}
