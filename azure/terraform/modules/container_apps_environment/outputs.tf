# Outputs for the Container Apps Environment module

output "id" {
  description = "The ID of the container apps environment"
  value       = azurerm_container_app_environment.env.id
}

output "name" {
  description = "The name of the container apps environment"
  value       = azurerm_container_app_environment.env.name
}

output "default_domain" {
  description = "The default domain of the container apps environment"
  value       = azurerm_container_app_environment.env.default_domain
}

output "static_ip_address" {
  description = "The static IP address of the container apps environment"
  value       = azurerm_container_app_environment.env.static_ip_address
}
