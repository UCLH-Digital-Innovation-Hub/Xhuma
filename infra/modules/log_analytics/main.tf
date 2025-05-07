# Azure Log Analytics Workspace Module

resource "azurerm_log_analytics_workspace" "workspace" {
  name                = "logxhuma${var.env_base_name}"
  location            = var.location
  resource_group_name = var.resource_group_name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = var.tags

  lifecycle {
    prevent_destroy = false  # Set to true for production environments
  }
}
