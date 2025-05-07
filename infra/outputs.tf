# Output values from Terraform deployment

output "acr_login_server" {
  description = "The login server URL for the Container Registry"
  value       = data.azurerm_container_registry.acr.login_server
}

output "container_app_url" {
  description = "The URL of the Xhuma container app"
  value       = module.container_apps.xhuma_app_url
}

output "redis_hostname" {
  description = "The hostname of the Redis cache"
  value       = module.redis.redis_hostname
  sensitive   = false
}

output "log_analytics_workspace_id" {
  description = "The ID of the Log Analytics workspace"
  value       = module.log_analytics.workspace_id
}

output "container_apps_environment_id" {
  description = "The ID of the Container Apps environment"
  value       = module.container_apps_environment.id
}

output "deployed_version" {
  description = "The version/tag of the deployed application"
  value       = var.image_tag
}

output "health_endpoint" {
  description = "The health check endpoint for the application"
  value       = "${module.container_apps.xhuma_app_url}/health"
}
