# Outputs for the ACR module

output "id" {
  description = "The ID of the container registry"
  value       = azurerm_container_registry.acr.id
}

output "login_server" {
  description = "The login server URL for the container registry"
  value       = azurerm_container_registry.acr.login_server
}

output "admin_username" {
  description = "The admin username for the container registry"
  value       = azurerm_container_registry.acr.admin_username
}

output "admin_password" {
  description = "The admin password for the container registry"
  value       = azurerm_container_registry.acr.admin_password
  sensitive   = true
}
