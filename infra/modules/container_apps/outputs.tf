output "xhuma_app_id" {
  description = "The ID of the Xhuma Container App"
  value       = azurerm_container_app.xhuma.id
}

output "xhuma_app_url" {
  description = "The URL of the Xhuma Container App"
  value       = azurerm_container_app.xhuma.ingress[0].fqdn
}

output "xhuma_app_latest_revision_name" {
  description = "The name of the latest revision of the Xhuma Container App"
  value       = azurerm_container_app.xhuma.latest_revision_name
}

output "xhuma_app_image" {
  description = "The image used by the Xhuma Container App"
  value       = "${var.acr_login_server}/xhuma:${var.image_tag}"
}

output "postgres_app_id" {
  description = "The ID of the PostgreSQL Container App"
  value       = var.enable_postgres ? azurerm_container_app.postgres[0].id : null
}

output "postgres_app_fqdn" {
  description = "The FQDN of the PostgreSQL Container App"
  value       = var.enable_postgres ? azurerm_container_app.postgres[0].ingress[0].fqdn : null
}
