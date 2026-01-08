output "webapp_url" {
  value = "https://${azurerm_linux_web_app.app.default_hostname}"
}

output "redis_hostname" {
  value = azurerm_redis_cache.redis.hostname
  sensitive = true
}
