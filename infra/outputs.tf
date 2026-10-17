output "webapp_url" {
  value = "https://${azurerm_linux_web_app.app.default_hostname}"
}

output "redis_hostname" {
  value     = azurerm_redis_cache.redis.hostname
  sensitive = true
}

output "postgres_hostname" {
  value     = azurerm_postgresql_flexible_server.postgres.fqdn
  sensitive = true
}

output "locust_mi_id" {
  value = azurerm_user_assigned_identity.locust_mi.id
}

output "locust_subnet_id" {
  value = azurerm_subnet.locust_subnet.id
}

output "app_subnet_id" {
  value       = azurerm_subnet.app_subnet.id
  description = "The ID of the App Service integration subnet, required for shared Key Vault network ACLs"
}

output "dmd_cache_warm_enabled" {
  value       = var.dmd_cache_warm_enabled
  description = "Whether the dm+d cache warmer infrastructure is enabled for this target"
}

output "dmd_cache_ca_subnet_id" {
  value       = try(azurerm_subnet.ca_subnet[0].id, null)
  description = "The ID of the Container Apps subnet for shared Key Vault network admission"
}

output "dmd_cache_job_name" {
  value       = try(azurerm_container_app_job.dmd_warm[0].name, null)
  description = "The name of the dm+d cache warmer Container Apps Job"
}

output "dmd_cache_job_image" {
  value       = try(azurerm_container_app_job.dmd_warm[0].template[0].container[0].image, null)
  description = "The configured container image on the Container Apps Job"
}
