output "redis_id" {
  description = "The ID of the Redis instance"
  value       = azurerm_redis_cache.redis.id
}

output "redis_hostname" {
  description = "The hostname of the Redis instance"
  value       = azurerm_redis_cache.redis.hostname
}

output "redis_port" {
  description = "The port of the Redis instance"
  value       = azurerm_redis_cache.redis.ssl_port
}

output "redis_primary_access_key" {
  description = "The primary access key for the Redis instance"
  value       = azurerm_redis_cache.redis.primary_access_key
  sensitive   = true
}

output "redis_connection_string" {
  description = "The connection string for the Redis instance"
  value       = "${azurerm_redis_cache.redis.hostname}:${azurerm_redis_cache.redis.ssl_port},password=${azurerm_redis_cache.redis.primary_access_key},ssl=True,abortConnect=False"
  sensitive   = true
}
