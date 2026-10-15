# Default App Service URL; the app's network access restrictions still apply.
output "webapp_url" {
  value = "https://${azurerm_linux_web_app.app.default_hostname}"
}

# Backend addresses for operational inspection; suppress them in normal CLI output.
output "redis_hostname" {
  value     = azurerm_redis_cache.redis.hostname
  sensitive = true
}

output "postgres_hostname" {
  value     = azurerm_postgresql_flexible_server.postgres.fqdn
  sensitive = true
}

# Resource handles for serverless Locust containers: identity and delegated subnet.
# load-test.yml currently discovers these same resources through the Azure CLI.
output "locust_mi_id" {
  value = azurerm_user_assigned_identity.locust_mi.id
}

output "locust_subnet_id" {
  value = azurerm_subnet.locust_subnet.id
}

# App Service integration subnet that must be allowed through the shared vault ACL.
# deploy-target.yml currently constructs this ID from the target manifest.
output "app_subnet_id" {
  value       = azurerm_subnet.app_subnet.id
  description = "The ID of the App Service integration subnet, required for shared Key Vault network ACLs"
}
