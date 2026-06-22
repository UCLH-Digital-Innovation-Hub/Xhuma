data "azurerm_resource_group" "rg" {
  name = var.resource_group_name
}

data "azurerm_client_config" "current" {}

data "azurerm_key_vault" "shared_kv" {
  name                = var.shared_key_vault_name
  resource_group_name = var.shared_resource_group_name
}

resource "azurerm_key_vault" "local_kv" {
  name                        = "${var.app_service_name}-kv"
  location                    = data.azurerm_resource_group.rg.location
  resource_group_name         = data.azurerm_resource_group.rg.name
  enabled_for_disk_encryption = true
  tenant_id                   = data.azurerm_client_config.current.tenant_id
  soft_delete_retention_days  = 7
  purge_protection_enabled    = false

  sku_name = "standard"
}

resource "azurerm_virtual_network" "vnet" {
  name                = "${var.app_service_name}-vnet"
  location            = data.azurerm_resource_group.rg.location
  resource_group_name = data.azurerm_resource_group.rg.name
  address_space       = ["10.1.0.0/16"]
}

resource "azurerm_subnet" "app_subnet" {
  name                 = "app-subnet"
  resource_group_name  = data.azurerm_resource_group.rg.name
  virtual_network_name = azurerm_virtual_network.vnet.name
  address_prefixes     = ["10.1.1.0/24"]
  delegation {
    name = "app-delegation"
    service_delegation {
      name    = "Microsoft.Web/serverFarms"
      actions = ["Microsoft.Network/virtualNetworks/subnets/action"]
    }
  }
}

resource "azurerm_subnet" "db_subnet" {
  name                 = "db-subnet"
  resource_group_name  = data.azurerm_resource_group.rg.name
  virtual_network_name = azurerm_virtual_network.vnet.name
  address_prefixes     = ["10.1.2.0/24"]
  delegation {
    name = "db-delegation"
    service_delegation {
      name    = "Microsoft.DBforPostgreSQL/flexibleServers"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_subnet" "pe_subnet" {
  name                 = "pe-subnet"
  resource_group_name  = data.azurerm_resource_group.rg.name
  virtual_network_name = azurerm_virtual_network.vnet.name
  address_prefixes     = ["10.1.3.0/24"]
}

resource "azurerm_private_dns_zone" "pg_dns" {
  name                = "privatelink.postgres.database.azure.com"
  resource_group_name = data.azurerm_resource_group.rg.name
}

resource "azurerm_private_dns_zone_virtual_network_link" "pg_dns_link" {
  name                  = "pg-vnet-link"
  private_dns_zone_name = azurerm_private_dns_zone.pg_dns.name
  virtual_network_id    = azurerm_virtual_network.vnet.id
  resource_group_name   = data.azurerm_resource_group.rg.name
}

resource "azurerm_private_dns_zone" "redis_dns" {
  name                = "privatelink.redis.cache.windows.net"
  resource_group_name = data.azurerm_resource_group.rg.name
}

resource "azurerm_private_dns_zone_virtual_network_link" "redis_dns_link" {
  name                  = "redis-vnet-link"
  private_dns_zone_name = azurerm_private_dns_zone.redis_dns.name
  virtual_network_id    = azurerm_virtual_network.vnet.id
  resource_group_name   = data.azurerm_resource_group.rg.name
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
  name                          = var.redis_name
  location                      = data.azurerm_resource_group.rg.location
  resource_group_name           = data.azurerm_resource_group.rg.name
  capacity                      = var.redis_capacity
  family                        = var.redis_family
  sku_name                      = var.redis_sku_name
  non_ssl_port_enabled          = false
  minimum_tls_version           = "1.2"
  public_network_access_enabled = false

  redis_configuration {
    maxmemory_reserved = 125
    maxmemory_delta    = 125
    maxmemory_policy   = "allkeys-lru"
  }
}

resource "azurerm_private_endpoint" "redis_pe" {
  name                = "${var.redis_name}-pe"
  location            = data.azurerm_resource_group.rg.location
  resource_group_name = data.azurerm_resource_group.rg.name
  subnet_id           = azurerm_subnet.pe_subnet.id

  private_service_connection {
    name                           = "redis-privatelink"
    private_connection_resource_id = azurerm_redis_cache.redis.id
    is_manual_connection           = false
    subresource_names              = ["redisCache"]
  }

  private_dns_zone_group {
    name                 = "redis-dns-group"
    private_dns_zone_ids = [azurerm_private_dns_zone.redis_dns.id]
  }
}

resource "azurerm_postgresql_flexible_server" "postgres" {
  name                          = var.postgres_server_name
  resource_group_name           = data.azurerm_resource_group.rg.name
  location                      = data.azurerm_resource_group.rg.location
  version                       = "15"
  administrator_login           = var.postgres_admin_username
  administrator_password        = var.postgres_admin_password
  storage_mb                    = 32768
  sku_name                      = "B_Standard_B1ms"
  backup_retention_days         = 7
  delegated_subnet_id           = azurerm_subnet.db_subnet.id
  private_dns_zone_id           = azurerm_private_dns_zone.pg_dns.id
  public_network_access_enabled = false

  depends_on = [azurerm_private_dns_zone_virtual_network_link.pg_dns_link]

  lifecycle {
    ignore_changes = [
      zone,
      high_availability.0.standby_availability_zone
    ]
  }
}

resource "azurerm_postgresql_flexible_server_database" "xhuma_db" {
  name      = "xhuma"
  server_id = azurerm_postgresql_flexible_server.postgres.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

resource "azurerm_linux_web_app" "app" {
  name                      = var.app_service_name
  resource_group_name       = data.azurerm_resource_group.rg.name
  location                  = data.azurerm_resource_group.rg.location
  service_plan_id           = azurerm_service_plan.plan.id
  virtual_network_subnet_id = azurerm_subnet.app_subnet.id

  identity {
    type = "SystemAssigned"
  }

  site_config {
    application_stack {
      docker_image     = lower(split(":", var.docker_image)[0])
      docker_image_tag = length(split(":", var.docker_image)) > 1 ? split(":", var.docker_image)[1] : "latest"
    }

    container_registry_use_managed_identity = false

    # Enable WebSockets for the Relay
    websockets_enabled     = true
    use_32_bit_worker      = true # Typically false for production but B1 is small
    vnet_route_all_enabled = true
  }

  # Enable mTLS: Optional allows public endpoints/health checks while passing the cert to the app
  client_certificate_enabled = true
  client_certificate_mode    = "Optional"

  app_settings = {
    "WEBSITES_ENABLE_APP_SERVICE_STORAGE" = "false"
    "DOCKER_REGISTRY_SERVER_URL"          = var.docker_registry_url
    "DOCKER_REGISTRY_SERVER_USERNAME"     = var.docker_registry_username
    "DOCKER_REGISTRY_SERVER_PASSWORD"     = var.docker_registry_password

    # App Config (Key Vault References)
    "API_KEY"           = "@Microsoft.KeyVault(VaultName=${var.shared_key_vault_name};SecretName=api-key)"
    "JWTKEY"            = "@Microsoft.KeyVault(VaultName=${var.shared_key_vault_name};SecretName=jwtkey)"
    "DMD_CLIENT_SECRET" = "@Microsoft.KeyVault(VaultName=${var.shared_key_vault_name};SecretName=dmd-client-secret)"
    "EPIC_CA_CERT"      = "@Microsoft.KeyVault(VaultName=${azurerm_key_vault.local_kv.name};SecretName=epic-ca-cert)"

    "REGISTRY_ID"    = var.registry_id
    "REDIS_HOST"     = azurerm_redis_cache.redis.hostname
    "REDIS_PORT"     = azurerm_redis_cache.redis.ssl_port
    "REDIS_PASSWORD" = azurerm_redis_cache.redis.primary_access_key
    "REDIS_SSL"      = "true"

    # Relay Configuration
    "USE_RELAY"                = "1" # Enabled by default for this deployment
    "EXTERNAL_RELAY_URL"       = var.external_relay_url
    "EXTERNAL_RELAY_CLIENT_ID" = var.external_relay_client_id
    "RELAY_CLIENT_CERT_HEADER" = "X-ARR-ClientCert" # Required for Azure App Service mTLS

    # Secure Key Vault References for Relay
    "EXTERNAL_RELAY_TOKEN"           = "@Microsoft.KeyVault(VaultName=${var.shared_key_vault_name};SecretName=external-relay-token)"
    "RELAY_MTLS_ALLOWED_CERT_SHA256" = "@Microsoft.KeyVault(VaultName=${var.shared_key_vault_name};SecretName=relay-mtls-allowed-cert-sha256)"

    # Postgres Config
    "POSTGRES_HOST"     = azurerm_postgresql_flexible_server.postgres.fqdn
    "POSTGRES_USER"     = var.postgres_admin_username
    "POSTGRES_PASSWORD" = var.postgres_admin_password
    "POSTGRES_DB"       = "xhuma"
    # Port is 5432 by default

    # Observability
    "APPLICATIONINSIGHTS_CONNECTION_STRING" = azurerm_application_insights.appinsights.connection_string
    "OTEL_SERVICE_NAME"                     = "xhuma"
    "OTEL_METRIC_EXPORT_INTERVAL_MS"        = var.otel_metric_export_interval_ms

    # Business Logic
    "ORG_CODE"  = var.org_code
    "ENV"       = var.env
    "VERSION"   = var.app_version
    "DEVICE_ID" = var.device_id
    "ORG_ASID"  = var.org_asid

    "GP_CONNECT_INCLUDE_ALLERGIES"      = var.gp_connect_include_allergies
    "GP_CONNECT_INCLUDE_MEDICATION"     = var.gp_connect_include_medication
    "GP_CONNECT_INCLUDE_PROBLEMS"       = var.gp_connect_include_problems
    "GP_CONNECT_INCLUDE_INVESTIGATIONS" = var.gp_connect_include_investigations
    "GP_CONNECT_INCLUDE_IMMUNISATIONS"  = var.gp_connect_include_immunisations

    # Security
    "CORS_ORIGINS"  = var.cors_origins
    "ALLOWED_HOSTS" = var.allowed_hosts
    "REQUIRE_MTLS"  = var.require_mtls
  }
}

# Access Policy for Local Vault
resource "azurerm_key_vault_access_policy" "app_local_policy" {
  key_vault_id = azurerm_key_vault.local_kv.id
  tenant_id    = data.azurerm_client_config.current.tenant_id
  object_id    = azurerm_linux_web_app.app.identity[0].principal_id

  secret_permissions      = ["Get", "List"]
  certificate_permissions = ["Get", "List"]
}

# Access Policy for Shared Vault
resource "azurerm_key_vault_access_policy" "app_shared_policy" {
  key_vault_id = data.azurerm_key_vault.shared_kv.id
  tenant_id    = data.azurerm_client_config.current.tenant_id
  object_id    = azurerm_linux_web_app.app.identity[0].principal_id

  secret_permissions      = ["Get", "List"]
  certificate_permissions = ["Get", "List"]
}
