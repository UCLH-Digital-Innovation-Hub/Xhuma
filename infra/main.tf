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
    
    # If using GHCR, we might not strictly need these if the image is public, 
    # but for private GHCR entries:
    container_registry_use_managed_identity = false
    
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
    
    # Observability
    "APPLICATIONINSIGHTS_CONNECTION_STRING" = azurerm_application_insights.appinsights.connection_string
    "OTEL_SERVICE_NAME"                     = "xhuma"
    # Additional Otel Vars will be set in main.py logic or here if using an agent.
    # The Azure Monitor Distro typically auto-configures via the connection string.
  }
}
