output "webapp_url" {
  value = "https://${azurerm_linux_web_app.app.default_hostname}"
}

output "redis_hostname" {
  value = azurerm_redis_cache.redis.hostname
  sensitive = true
}

output "postgres_hostname" {
  value = azurerm_postgresql_flexible_server.postgres.fqdn
  sensitive = true
}
