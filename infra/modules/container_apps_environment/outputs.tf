output "id" {
  description = "The ID of the Container Apps Environment"
  value       = azurerm_container_app_environment.env.id
}

output "name" {
  description = "The name of the Container Apps Environment"
  value       = azurerm_container_app_environment.env.name
}

output "default_domain" {
  description = "The default domain of the Container Apps Environment"
  value       = azurerm_container_app_environment.env.default_domain
}

output "static_ip" {
  description = "The static IP of the Container Apps Environment"
  value       = azurerm_container_app_environment.env.static_ip_address
}
