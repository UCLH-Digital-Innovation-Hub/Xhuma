# Xhuma Azure Deployment - Terraform Configuration
# Simplified Deployment Pipeline for Azure Container Apps

terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.75.0"
    }
  }

  # Configure state backend - using local for now
  # TODO: Switch to remote backend in the future
  # Example of Azure Storage backend:
  # backend "azurerm" {
  #   resource_group_name  = "rg-xhuma-play"
  #   storage_account_name = "stxhumaplayterraform"
  #   container_name       = "tfstate"
  #   key                  = "terraform.tfstate"
  # }
}

provider "azurerm" {
  features {}
  skip_provider_registration = true
}

# Reference the existing resource group
data "azurerm_resource_group" "rg" {
  name = var.resource_group_name # "rg-xhuma-play"
}

# Extract base name for resource naming
locals {
  env_base_name = replace(replace(var.resource_group_name, "rg-xhuma-", ""), "rg-", "")
  default_tags = {
    Environment = local.env_base_name
    Application = "Xhuma"
    ManagedBy   = "Terraform"
  }
}

# Reference existing ACR if it exists
data "azurerm_container_registry" "acr" {
  name                = "crxhuma${local.env_base_name}"
  resource_group_name = var.resource_group_name
}

# Create Log Analytics Workspace
module "log_analytics" {
  source              = "./modules/log_analytics"
  resource_group_name = var.resource_group_name
  location            = var.location
  env_base_name       = local.env_base_name
  tags                = local.default_tags
}

# Create Container Apps Environment
module "container_apps_environment" {
  source                         = "./modules/container_apps_environment"
  resource_group_name            = var.resource_group_name
  location                       = var.location
  env_base_name                  = local.env_base_name
  log_analytics_workspace_id     = module.log_analytics.workspace_id
  tags                           = local.default_tags
}

# Create Azure Cache for Redis
module "redis" {
  source              = "./modules/redis"
  resource_group_name = var.resource_group_name
  location            = var.location
  env_base_name       = local.env_base_name
  redis_password      = var.redis_password
  tags                = local.default_tags
}

# Validate that jwtkey is provided
resource "null_resource" "validate_jwtkey" {
  # This will fail if jwtkey is not set
  provisioner "local-exec" {
    command = <<-EOT
      if [ -z "${var.jwtkey}" ]; then
        echo "Error: jwtkey variable must be provided for secure JWT key handling."
        exit 1
      fi
    EOT
    interpreter = ["/bin/bash", "-c"]
  }

  # Trigger validation on any change to the jwtkey variable
  triggers = {
    jwtkey_provided = var.jwtkey != "" ? "true" : "error: jwtkey is required"
  }
}

# Deploy Container Apps
module "container_apps" {
  source                      = "./modules/container_apps"
  resource_group_name         = var.resource_group_name
  location                    = var.location
  env_base_name               = local.env_base_name
  container_app_environment_id = module.container_apps_environment.id
  acr_login_server            = data.azurerm_container_registry.acr.login_server
  acr_admin_username          = data.azurerm_container_registry.acr.admin_username
  acr_admin_password          = data.azurerm_container_registry.acr.admin_password
  redis_host                  = module.redis.redis_hostname
  redis_password              = var.redis_password
  postgres_password           = var.postgres_password # Optional: Used if enabling Postgres
  api_key                     = var.api_key
  jwtkey                      = var.jwtkey  # Pass the JWT signing key to the container apps
  grafana_admin_password      = var.grafana_admin_password
  image_tag                   = var.image_tag
  tags                        = local.default_tags
  redis_dependency            = module.redis

  # Ensure validation happens before container app deployment
  depends_on = [null_resource.validate_jwtkey, module.redis]
}
