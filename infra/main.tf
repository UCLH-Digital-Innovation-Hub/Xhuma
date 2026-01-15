data "azurerm_resource_group" "rg" {
  name = var.resource_group_name
}

resource "azurerm_service_plan" "plan" {
  name                = "${var.app_service_name}-plan"
  resource_group_name = data.azurerm_resource_group.rg.name
  location            = data.azurerm_resource_group.rg.location
  os_type             = "Linux"
  sku_name            = "B1" # Can be scaled up to S1 or P1v2 as needed
}

resource "azurerm_log_analytics_workspace" "law" {
  name                = "${var.app_service_name}-law"
  location            = data.azurerm_resource_group.rg.location
  resource_group_name = data.azurerm_resource_group.rg.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
}

resource "azurerm_application_insights" "appinsights" {
  name                = "${var.app_service_name}-ai"
  location            = data.azurerm_resource_group.rg.location
  resource_group_name = data.azurerm_resource_group.rg.name
  workspace_id        = azurerm_log_analytics_workspace.law.id
  application_type    = "web"
}

resource "azurerm_redis_cache" "redis" {
  name                = var.redis_name
  location            = data.azurerm_resource_group.rg.location
  resource_group_name = data.azurerm_resource_group.rg.name
  capacity            = var.redis_capacity
  family              = var.redis_family
  sku_name            = var.redis_sku_name
  enable_non_ssl_port = false
  minimum_tls_version = "1.2"

  redis_configuration {
    maxmemory_reserved = 2
    maxmemory_delta    = 2
    maxmemory_policy   = "allkeys-lru"
  }
}

resource "azurerm_postgresql_flexible_server" "postgres" {
  name                   = var.postgres_server_name
  resource_group_name    = data.azurerm_resource_group.rg.name
  location               = data.azurerm_resource_group.rg.location
  version                = "15"
  administrator_login    = var.postgres_admin_username
  administrator_login_password = var.postgres_admin_password
  storage_mb             = 32768
  sku_name               = "B_Standard_B1ms"
  backup_retention_days  = 7
  
  # For simplified deployment, we allow public access (controlled by firewall rules)
  # Ideally use VNET integration in production
}

resource "azurerm_postgresql_flexible_server_firewall_rule" "allow_azure_services" {
  name             = "allow_azure_services"
  server_id        = azurerm_postgresql_flexible_server.postgres.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0" # This specific rule allows access from Azure services
}

resource "azurerm_linux_web_app" "app" {
  name                = var.app_service_name
  resource_group_name = data.azurerm_resource_group.rg.name
  location            = data.azurerm_resource_group.rg.location
  service_plan_id     = azurerm_service_plan.plan.id

  site_config {
    application_stack {
      docker_image     = split(":", var.docker_image)[0]
      docker_image_tag = length(split(":", var.docker_image)) > 1 ? split(":", var.docker_image)[1] : "latest"
    }
    
    container_registry_use_managed_identity = false
    
    # Enable WebSockets for the Relay
    websockets_enabled = true
    use_32_bit_worker_process = true # Typically false for production but B1 is small
  }

  app_settings = {
    "WEBSITES_ENABLE_APP_SERVICE_STORAGE" = "false"
    "DOCKER_REGISTRY_SERVER_URL"          = var.docker_registry_url
    "DOCKER_REGISTRY_SERVER_USERNAME"     = var.docker_registry_username
    "DOCKER_REGISTRY_SERVER_PASSWORD"     = var.docker_registry_password

    # App Config
    "API_KEY"              = var.api_key
    "JWTKEY"               = var.jwt_key
    "REGISTRY_ID"          = var.registry_id
    "REDIS_HOST"           = azurerm_redis_cache.redis.hostname
    "REDIS_PORT"           = azurerm_redis_cache.redis.ssl_port
    "REDIS_PASSWORD"       = azurerm_redis_cache.redis.primary_access_key
    "REDIS_SSL"            = "true"

    # Relay Configuration
    "USE_RELAY"            = "1" # Enabled by default for this deployment

    # Postgres Config
    "POSTGRES_HOST"        = azurerm_postgresql_flexible_server.postgres.fqdn
    "POSTGRES_USER"        = var.postgres_admin_username
    "POSTGRES_PASSWORD"    = var.postgres_admin_password
    "POSTGRES_DB"          = "xhuma"
    # Port is 5432 by default
    
    # Observability
    "APPLICATIONINSIGHTS_CONNECTION_STRING" = azurerm_application_insights.appinsights.connection_string
    "OTEL_SERVICE_NAME"                     = "xhuma"
  }
}
