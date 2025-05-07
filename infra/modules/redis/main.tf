# Azure Cache for Redis Module

resource "azurerm_redis_cache" "redis" {
  name                = "redis-xhuma-${var.env_base_name}"
  location            = var.location
  resource_group_name = var.resource_group_name
  capacity            = 1
  family              = "C"
  sku_name            = "Basic"
  enable_non_ssl_port = false
  minimum_tls_version = "1.2"
  
  redis_configuration {
    maxmemory_policy = "volatile-lru"
  }

  tags = var.tags

  lifecycle {
    prevent_destroy = false  # Set to true for production environments
  }
}

# Store Redis password in Key Vault (optional - uncomment when Key Vault is available)
# resource "azurerm_key_vault_secret" "redis_password" {
#   name         = "redis-password"
#   value        = var.redis_password
#   key_vault_id = var.key_vault_id
# }
