terraform {
  backend "azurerm" {}
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.0"
    }
  }
}

provider "azurerm" {
  features {}
}

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
  enable_rbac_authorization       = false
  public_network_access_enabled   = var.public_network_access_enabled

  lifecycle {
    prevent_destroy = true
    ignore_changes = [
      # Crucial: Allow target states to manage their own access policies safely
      access_policy,
      contact,
      tags["CostCenter"]
    ]
  }

  network_acls {
    default_action             = "Deny"
    bypass                     = "AzureServices"
    ip_rules                   = var.approved_ip_rules
    virtual_network_subnet_ids = var.approved_subnet_ids
  }
}
