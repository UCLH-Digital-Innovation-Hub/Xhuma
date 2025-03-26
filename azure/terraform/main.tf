# Main Terraform configuration file for Xhuma Azure deployment

terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.0"
    }
    azuread = {
      source  = "hashicorp/azuread"
      version = "~> 2.0"
    }
  }
  
  # Backend can be configured later when remote state is needed
  # backend "azurerm" { }
}

provider "azurerm" {
  features {}
  # Note: Tenant and subscription should be set via az CLI before running Terraform
  # az login --tenant uclhaz.onmicrosoft.com
  # az account set --subscription rg-xhuma-play
}

provider "azuread" {}

# Local variables
locals {
  # Common tags for all resources according to UCL requirements
  common_tags = {
    CostCenter    = "240300"
    CreatedBy     = var.created_by
    Environment   = var.environment
    git_origin    = var.git_origin
    ManagedBy     = "Terraform"
    Owner         = var.owner
    "Service Hours" = var.service_hours
  }
  
  # Extract the environment name without prefixes for use in resource naming
  # If environment is like "rg-xhuma-play", extract just "play"
  env_base_name = replace(
    replace(var.environment, "rg-xhuma-", ""),
    "rg-", ""
  )
  
  # Derive resource group name if not explicitly provided
  # Avoid doubling prefixes by checking if environment already contains the prefix
  resource_group_name = var.resource_group_name != "" ? var.resource_group_name : (
    startswith(var.environment, "rg-") ? var.environment : "rg-xhuma-${var.environment}"
  )
}

# Use existing resource group
data "azurerm_resource_group" "main" {
  name = local.resource_group_name
}

# Azure Container Registry
module "acr" {
  source              = "./modules/acr"
  name                = "cr-xhuma-${var.environment}"
  resource_group_name = data.azurerm_resource_group.main.name
  location            = var.location
  sku                 = "Standard"
  admin_enabled       = true
  tags                = local.common_tags
}

# Storage Account for file shares
module "storage" {
  source                    = "./modules/storage"
  name                      = "st${var.environment}xhuma"  # Storage accounts can't have hyphens
  resource_group_name       = data.azurerm_resource_group.main.name
  location                  = var.location
  account_tier              = "Standard"
  account_replication_type  = "LRS"
  file_share_names          = ["redis-data", "postgres-data"]
  file_share_quota          = 100
  tags                      = local.common_tags
}

# Key Vault for secrets
module "key_vault" {
  source              = "./modules/key_vault"
  name                = "kv-xhuma-${var.environment}"
  resource_group_name = data.azurerm_resource_group.main.name
  location            = var.location
  sku_name            = "standard"
  tenant_id           = data.azurerm_client_config.current.tenant_id
  tags                = local.common_tags
}

# Log Analytics workspace for monitoring
module "log_analytics" {
  source              = "./modules/log_analytics"
  name                = "log-xhuma-${var.environment}"
  resource_group_name = data.azurerm_resource_group.main.name
  location            = var.location
  retention_in_days   = 30
  tags                = local.common_tags
}

# Container Apps Environment
module "container_apps_environment" {
  source                       = "./modules/container_apps_environment"
  name                         = "cae-xhuma-${var.environment}"
  resource_group_name          = data.azurerm_resource_group.main.name
  location                     = var.location
  log_analytics_workspace_id   = module.log_analytics.workspace_id
  log_analytics_workspace_key  = module.log_analytics.primary_shared_key
  tags                         = local.common_tags
}

# Container Apps
module "container_apps" {
  source                      = "./modules/container_apps"
  resource_group_name         = data.azurerm_resource_group.main.name
  location                    = var.location
  environment                 = var.environment
  container_app_environment_id = module.container_apps_environment.id
  acr_login_server            = module.acr.login_server
  acr_admin_username          = module.acr.admin_username
  acr_admin_password          = module.acr.admin_password
  storage_account_name        = module.storage.name
  storage_account_key         = module.storage.primary_access_key
  key_vault_id                = module.key_vault.id
  redis_password              = var.redis_password
  postgres_password           = var.postgres_password
  api_key                     = var.api_key
  grafana_admin_password      = var.grafana_admin_password
  tags                        = local.common_tags
}

# Current client config for Key Vault access policies
data "azurerm_client_config" "current" {}

# Outputs
output "acr_login_server" {
  value     = module.acr.login_server
  sensitive = false
}

output "acr_admin_username" {
  value     = module.acr.admin_username
  sensitive = false
}

output "acr_admin_password" {
  value     = module.acr.admin_password
  sensitive = true
}

output "storage_account_name" {
  value     = module.storage.name
  sensitive = false
}

output "key_vault_uri" {
  value     = module.key_vault.vault_uri
  sensitive = false
}

output "container_apps_environment_default_domain" {
  value     = module.container_apps_environment.default_domain
  sensitive = false
}

output "xhuma_app_url" {
  value     = module.container_apps.xhuma_app_url
  sensitive = false
}
