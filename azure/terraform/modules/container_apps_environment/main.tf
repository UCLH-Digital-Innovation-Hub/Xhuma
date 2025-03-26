# Azure Container Apps Environment module

resource "azurerm_container_app_environment" "env" {
  name                       = var.name
  resource_group_name        = var.resource_group_name
  location                   = var.location
  log_analytics_workspace_id = var.log_analytics_workspace_id
  infrastructure_subnet_id   = var.infrastructure_subnet_id
  tags                       = var.tags
}
