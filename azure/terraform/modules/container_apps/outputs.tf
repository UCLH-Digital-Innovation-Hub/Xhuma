# Outputs for the Container Apps module

output "xhuma_app_id" {
  description = "The ID of the Xhuma container app"
  value       = azurerm_container_app.xhuma.id
}

output "xhuma_app_url" {
  description = "The URL of the Xhuma container app"
  value       = "https://${azurerm_container_app.xhuma.ingress[0].fqdn}"
}

output "redis_app_id" {
  description = "The ID of the Redis container app"
  value       = azurerm_container_app.redis.id
}

output "redis_app_fqdn" {
  description = "The FQDN of the Redis container app"
  value       = azurerm_container_app.redis.ingress[0].fqdn
}

output "postgres_app_id" {
  description = "The ID of the PostgreSQL container app"
  value       = azurerm_container_app.postgres.id
}

output "postgres_app_fqdn" {
  description = "The FQDN of the PostgreSQL container app"
  value       = azurerm_container_app.postgres.ingress[0].fqdn
}

output "prometheus_app_id" {
  description = "The ID of the Prometheus container app"
  value       = azurerm_container_app.prometheus.id
}

output "grafana_app_id" {
  description = "The ID of the Grafana container app"
  value       = azurerm_container_app.grafana.id
}

output "otel_collector_app_id" {
  description = "The ID of the OpenTelemetry collector container app"
  value       = azurerm_container_app.otel_collector.id
}

output "tempo_app_id" {
  description = "The ID of the Tempo container app"
  value       = azurerm_container_app.tempo.id
}
