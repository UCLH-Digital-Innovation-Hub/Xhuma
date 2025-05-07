# Azure Container Apps Environment Module

resource "azurerm_container_app_environment" "env" {
  name                       = "caexhuma${var.env_base_name}"
  location                   = var.location
  resource_group_name        = var.resource_group_name
  log_analytics_workspace_id = var.log_analytics_workspace_id
  tags                       = var.tags

  lifecycle {
    prevent_destroy = false  # Set to true for production environments
  }
}
