# Azure Container Apps Environment module

resource "azurerm_container_app_environment" "env" {
  name                       = var.name
  resource_group_name        = var.resource_group_name
  location                   = var.location
  log_analytics_workspace_id = var.log_analytics_workspace_id
  infrastructure_subnet_id   = var.infrastructure_subnet_id
  tags                       = var.tags
  
  # Add comprehensive lifecycle configuration to handle existing environments properly
  lifecycle {
    # Prevent destroy protects from accidental deletion
    prevent_destroy = true
    
    # Ignore changes to fields that might cause replacement
    # This ensures existing environments aren't recreated
    ignore_changes = [
      log_analytics_workspace_id,
      infrastructure_subnet_id,
      tags
    ]
  }
}

# Create storage for Redis and PostgreSQL
resource "azurerm_container_app_environment_storage" "redis_data" {
  name                         = "redis-data"
  container_app_environment_id = azurerm_container_app_environment.env.id
  account_name                 = var.storage_account_name
  access_key                   = var.storage_account_key
  share_name                   = "redis-data"
  access_mode                  = "ReadWrite"
}

resource "azurerm_container_app_environment_storage" "postgres_data" {
  name                         = "postgres-data"
  container_app_environment_id = azurerm_container_app_environment.env.id
  account_name                 = var.storage_account_name
  access_key                   = var.storage_account_key
  share_name                   = "postgres-data"
  access_mode                  = "ReadWrite"
}
