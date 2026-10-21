# Workload root: each target initializes its own remote state using the backend
# file selected by infra/targets.json; provider versions stay on AzureRM 3.x.
terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.0"
    }
  }
  backend "azurerm" {}
}

# Default provider manages resources in the target subscription (ARM_SUBSCRIPTION_ID).
provider "azurerm" {
  features {
    key_vault {
      # Retain soft-deleted vaults on destroy and recover matching vaults on create.
      purge_soft_delete_on_destroy    = false
      recover_soft_deleted_key_vaults = true
    }
  }
}

# Use the shared subscription for vault lookup and target-owned access policies.
# The vault itself and its firewall belong to the separate infra/shared root.
provider "azurerm" {
  alias           = "shared"
  subscription_id = var.shared_subscription_id
  features {}
}
