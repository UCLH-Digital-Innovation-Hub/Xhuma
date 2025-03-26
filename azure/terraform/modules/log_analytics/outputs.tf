# Outputs for the Log Analytics module

output "id" {
  description = "The ID of the log analytics workspace"
  value       = azurerm_log_analytics_workspace.workspace.id
}

output "workspace_id" {
  description = "The workspace ID of the log analytics workspace"
  value       = azurerm_log_analytics_workspace.workspace.workspace_id
}

output "primary_shared_key" {
  description = "The primary shared key of the log analytics workspace"
  value       = azurerm_log_analytics_workspace.workspace.primary_shared_key
  sensitive   = true
}

output "secondary_shared_key" {
  description = "The secondary shared key of the log analytics workspace"
  value       = azurerm_log_analytics_workspace.workspace.secondary_shared_key
  sensitive   = true
}
