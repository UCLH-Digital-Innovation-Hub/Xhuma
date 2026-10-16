data "azurerm_resource_group" "rg" {
  name = var.resource_group_name
}

data "azurerm_client_config" "current" {}

data "azurerm_key_vault" "shared_kv" {
  provider            = azurerm.shared
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

  lifecycle {
    ignore_changes = [tags["CostCenter"]]
  }
}

resource "azurerm_virtual_network" "vnet" {
  name                = "${var.app_service_name}-vnet"
  location            = data.azurerm_resource_group.rg.location
  resource_group_name = data.azurerm_resource_group.rg.name
  address_space       = ["10.1.0.0/16"]

  lifecycle {
    ignore_changes = [tags["CostCenter"]]
  }
}

resource "azurerm_subnet" "app_subnet" {
  name                 = "app-subnet"
  resource_group_name  = data.azurerm_resource_group.rg.name
  virtual_network_name = azurerm_virtual_network.vnet.name
  address_prefixes     = ["10.1.1.0/24"]
  service_endpoints    = ["Microsoft.KeyVault"]
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

  service_endpoints = ["Microsoft.Storage"]
}

resource "azurerm_subnet" "pe_subnet" {
  name                 = "pe-subnet"
  resource_group_name  = data.azurerm_resource_group.rg.name
  virtual_network_name = azurerm_virtual_network.vnet.name
  address_prefixes     = ["10.1.3.0/24"]
}

resource "azurerm_subnet" "locust_subnet" {
  name                 = "locust-subnet"
  resource_group_name  = data.azurerm_resource_group.rg.name
  virtual_network_name = azurerm_virtual_network.vnet.name
  address_prefixes     = ["10.1.4.0/24"]
  delegation {
    name = "locust-delegation"
    service_delegation {
      name    = "Microsoft.ContainerInstance/containerGroups"
      actions = ["Microsoft.Network/virtualNetworks/subnets/action"]
    }
  }
}

resource "azurerm_subnet" "ca_subnet" {
  count                = var.dmd_cache_warm_enabled ? 1 : 0
  name                 = "ca-subnet"
  resource_group_name  = data.azurerm_resource_group.rg.name
  virtual_network_name = azurerm_virtual_network.vnet.name
  address_prefixes     = ["10.1.5.0/27"]
  service_endpoints    = ["Microsoft.KeyVault"]

  delegation {
    name = "ca-delegation"
    service_delegation {
      name    = "Microsoft.App/environments"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_user_assigned_identity" "locust_mi" {
  location            = data.azurerm_resource_group.rg.location
  name                = "${var.app_service_name}-locust-mi"
  resource_group_name = data.azurerm_resource_group.rg.name

  lifecycle {
    ignore_changes = [tags["CostCenter"]]
  }
}

resource "azurerm_private_dns_zone" "pg_dns" {
  name                = "privatelink.postgres.database.azure.com"
  resource_group_name = data.azurerm_resource_group.rg.name

  lifecycle {
    ignore_changes = [tags["CostCenter"]]
  }
}

resource "azurerm_private_dns_zone_virtual_network_link" "pg_dns_link" {
  name                  = "pg-vnet-link"
  private_dns_zone_name = azurerm_private_dns_zone.pg_dns.name
  virtual_network_id    = azurerm_virtual_network.vnet.id
  resource_group_name   = data.azurerm_resource_group.rg.name

  lifecycle {
    ignore_changes = [tags["CostCenter"]]
  }
}

resource "azurerm_private_dns_zone" "redis_dns" {
  name                = "privatelink.redis.cache.windows.net"
  resource_group_name = data.azurerm_resource_group.rg.name

  lifecycle {
    ignore_changes = [tags["CostCenter"]]
  }
}

resource "azurerm_private_dns_zone_virtual_network_link" "redis_dns_link" {
  name                  = "redis-vnet-link"
  private_dns_zone_name = azurerm_private_dns_zone.redis_dns.name
  virtual_network_id    = azurerm_virtual_network.vnet.id
  resource_group_name   = data.azurerm_resource_group.rg.name

  lifecycle {
    ignore_changes = [tags["CostCenter"]]
  }
}

resource "azurerm_service_plan" "plan" {
  name                = "${var.app_service_name}-plan"
  resource_group_name = data.azurerm_resource_group.rg.name
  location            = data.azurerm_resource_group.rg.location
  os_type             = "Linux"
  sku_name            = "B1" # Can be scaled up to S1 or P1v2 as needed

  lifecycle {
    ignore_changes = [tags["CostCenter"]]
  }
}

resource "azurerm_log_analytics_workspace" "law" {
  name                = "${var.app_service_name}-law"
  location            = data.azurerm_resource_group.rg.location
  resource_group_name = data.azurerm_resource_group.rg.name
  sku                 = "PerGB2018"
  retention_in_days   = 30

  lifecycle {
    ignore_changes = [tags["CostCenter"]]
  }
}

resource "azurerm_application_insights" "appinsights" {
  name                = "${var.app_service_name}-ai"
  location            = data.azurerm_resource_group.rg.location
  resource_group_name = data.azurerm_resource_group.rg.name
  workspace_id        = azurerm_log_analytics_workspace.law.id
  application_type    = "web"

  lifecycle {
    ignore_changes = [tags["CostCenter"]]
  }
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

  lifecycle {
    ignore_changes = [tags["CostCenter"]]
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

  lifecycle {
    ignore_changes = [tags["CostCenter"]]
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
      high_availability.0.standby_availability_zone,
      tags["CostCenter"]
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

  lifecycle {
    ignore_changes = [
      site_config[0].application_stack[0].docker_image_name,
      app_settings["WEBSITE_VNET_ROUTE_ALL"],
      tags["CostCenter"],
      tags["hidden-link: /app-insights-resource-id"]
    ]
  }

  site_config {
    application_stack {
      docker_image_name        = var.docker_image
      docker_registry_url      = var.docker_registry_url
      docker_registry_username = var.docker_registry_username
      docker_registry_password = var.docker_registry_password
    }

    container_registry_use_managed_identity = false

    # Enable WebSockets for the Relay
    websockets_enabled     = true
    use_32_bit_worker      = true # Typically false for production but B1 is small
    vnet_route_all_enabled = true

    ip_restriction {
      action   = "Allow"
      name     = "AllowHSCN"
      priority = 100
      # Placeholder for actual HSCN or Epic IP ranges
      ip_address = "192.168.0.0/16"
    }

    scm_ip_restriction {
      action                    = "Allow"
      name                      = "AllowVnet"
      priority                  = 100
      virtual_network_subnet_id = azurerm_subnet.app_subnet.id
    }
  }

  # Enable mTLS: Optional allows public endpoints/health checks while passing the cert to the app
  client_certificate_enabled = true
  client_certificate_mode    = "Optional"

  app_settings = {
    "WEBSITES_ENABLE_APP_SERVICE_STORAGE" = "false"

    # App Config (Key Vault References)
    "API_KEY"           = "@Microsoft.KeyVault(VaultName=${var.shared_key_vault_name};SecretName=api-key)"
    "NHS_API_KEY"       = "@Microsoft.KeyVault(VaultName=${var.shared_key_vault_name};SecretName=nhs-api-key)"
    "KEY_VAULT_URL"     = data.azurerm_key_vault.shared_kv.vault_uri
    "DMD_CLIENT_ID"     = "@Microsoft.KeyVault(VaultName=${var.shared_key_vault_name};SecretName=dmd-client-id)"
    "DMD_CLIENT_SECRET" = "@Microsoft.KeyVault(VaultName=${var.shared_key_vault_name};SecretName=dmd-client-secret)"
    "DMD_BASE_URL"      = var.dmd_base_url
    "EPIC_CA_CERT"      = "@Microsoft.KeyVault(VaultName=${azurerm_key_vault.local_kv.name};SecretName=epic-ca-cert)"

    # NHS GP Connect Certificates (Shared Vault) - Required only for direct/non-relay HTTP path
    "NHS_CLIENT_CERT" = var.nhs_over_internet_path != "" ? "@Microsoft.KeyVault(VaultName=${var.shared_key_vault_name};SecretName=xhuma-nhs-client-cert)" : null
    "NHS_CLIENT_KEY"  = var.nhs_over_internet_path != "" ? "@Microsoft.KeyVault(VaultName=${var.shared_key_vault_name};SecretName=xhuma-nhs-client-key)" : null
    "NHS_BUNDLE"      = var.nhs_over_internet_path != "" ? "@Microsoft.KeyVault(VaultName=${var.shared_key_vault_name};SecretName=xhuma-nhs-bundle)" : null

    # New Security Mitigations
    "MTLS_TRUSTED_THUMBPRINTS" = "@Microsoft.KeyVault(VaultName=${var.shared_key_vault_name};SecretName=mtls-trusted-thumbprints)"
    "SAML_TRUSTED_ISSUER"      = "@Microsoft.KeyVault(VaultName=${var.shared_key_vault_name};SecretName=saml-trusted-issuer)"
    "ALLOWED_REPLY_TO_DOMAINS" = ".nhs.uk"

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

    # NHS Relay endpoints
    "NHS_RELAY_BASE_PATH"    = var.nhs_relay_base_path
    "NHS_OVER_INTERNET_PATH" = var.nhs_over_internet_path

    # Secure Key Vault References for Relay
    "EXTERNAL_RELAY_TOKEN"           = var.external_relay_url != "" ? "@Microsoft.KeyVault(VaultName=${var.shared_key_vault_name};SecretName=external-relay-token)" : null
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
    "ORG_CODE"            = var.org_code
    "ENV"                 = var.env
    "CCDA_EXPIRY_HOURS"   = var.ccda_expiry_hours
    "VERSION"             = var.app_version
    "DEVICE_ID"           = var.device_id
    "ORG_ASID"            = var.org_asid
    "XHUMA_ODS_CODE"      = var.xhuma_ods_code
    "XHUMA_MHS_PARTY_KEY" = var.xhuma_mhs_party_key

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
  provider     = azurerm.shared
  key_vault_id = data.azurerm_key_vault.shared_kv.id
  tenant_id    = data.azurerm_client_config.current.tenant_id
  object_id    = azurerm_linux_web_app.app.identity[0].principal_id

  secret_permissions      = ["Get", "List"]
  certificate_permissions = ["Get", "List"]
}

# Locust MI Access Policy for Local Vault
resource "azurerm_key_vault_access_policy" "locust_local_policy" {
  key_vault_id = azurerm_key_vault.local_kv.id
  tenant_id    = data.azurerm_client_config.current.tenant_id
  object_id    = azurerm_user_assigned_identity.locust_mi.principal_id

  secret_permissions      = ["Get", "List"]
  certificate_permissions = ["Get", "List"]
}

# Locust MI Access Policy for Shared Vault
resource "azurerm_key_vault_access_policy" "locust_shared_policy" {
  provider     = azurerm.shared
  key_vault_id = data.azurerm_key_vault.shared_kv.id
  tenant_id    = data.azurerm_client_config.current.tenant_id
  object_id    = azurerm_user_assigned_identity.locust_mi.principal_id

  secret_permissions      = ["Get", "List"]
  certificate_permissions = ["Get", "List"]
}

# --- PostgreSQL Audit Logging Configuration ---

resource "azurerm_monitor_diagnostic_setting" "postgres_diag" {
  name                       = "postgres-audit-logs"
  target_resource_id         = azurerm_postgresql_flexible_server.postgres.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.law.id

  enabled_log {
    category = "PostgreSQLLogs"
  }

  metric {
    category = "AllMetrics"
    enabled  = true
  }
}

resource "azurerm_postgresql_flexible_server_configuration" "log_connections" {
  name      = "log_connections"
  server_id = azurerm_postgresql_flexible_server.postgres.id
  value     = "on"
}

resource "azurerm_postgresql_flexible_server_configuration" "log_disconnections" {
  name      = "log_disconnections"
  server_id = azurerm_postgresql_flexible_server.postgres.id
  value     = "on"
}

resource "azurerm_postgresql_flexible_server_configuration" "log_checkpoints" {
  name      = "log_checkpoints"
  server_id = azurerm_postgresql_flexible_server.postgres.id
  value     = "on"
}

resource "azurerm_postgresql_flexible_server_configuration" "log_statement" {
  name      = "log_statement"
  server_id = azurerm_postgresql_flexible_server.postgres.id
  value     = "ddl"
}

resource "azurerm_postgresql_flexible_server_configuration" "log_min_duration_statement" {
  name      = "log_min_duration_statement"
  server_id = azurerm_postgresql_flexible_server.postgres.id
  value     = "-1"
}

# --- Target-Local dm+d Cache Warmer Infrastructure ---

locals {
  dmd_job_replica_timeout_seconds = 1800
  dmd_logic_poll_timeout_seconds  = 2100
  dmd_lock_ttl_seconds            = 2700
}

resource "azurerm_container_app_environment" "cae" {
  count                          = var.dmd_cache_warm_enabled ? 1 : 0
  name                           = "${var.app_service_name}-cae"
  location                       = data.azurerm_resource_group.rg.location
  resource_group_name            = data.azurerm_resource_group.rg.name
  infrastructure_subnet_id       = azurerm_subnet.ca_subnet[0].id
  log_analytics_workspace_id     = azurerm_log_analytics_workspace.law.id
  internal_load_balancer_enabled = false

  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }

  # AzureRM/Azure normalise the Consumption profile after creation, and Azure
  # generates the infrastructure resource-group name behind the scenes. Neither
  # value is an operator-controlled configuration input; ignoring them prevents
  # false destroy/recreate plans on subsequent runs while first creation still
  # uses the declared Consumption profile.
  lifecycle {
    ignore_changes = [
      workload_profile,
      infrastructure_resource_group_name,
      tags["CostCenter"],
    ]
  }
}

resource "azurerm_user_assigned_identity" "dmd_job_mi" {
  count               = var.dmd_cache_warm_enabled ? 1 : 0
  name                = "${var.app_service_name}-dmd-job-mi"
  location            = data.azurerm_resource_group.rg.location
  resource_group_name = data.azurerm_resource_group.rg.name

  lifecycle {
    ignore_changes = [tags["CostCenter"]]
  }
}

resource "azurerm_key_vault_access_policy" "dmd_job_shared_policy" {
  count        = var.dmd_cache_warm_enabled ? 1 : 0
  provider     = azurerm.shared
  key_vault_id = data.azurerm_key_vault.shared_kv.id
  tenant_id    = data.azurerm_client_config.current.tenant_id
  object_id    = azurerm_user_assigned_identity.dmd_job_mi[0].principal_id

  secret_permissions = ["Get"]
}

resource "azurerm_container_app_job" "dmd_warm" {
  count                        = var.dmd_cache_warm_enabled ? 1 : 0
  name                         = "${var.app_service_name}-dmd-warm"
  location                     = data.azurerm_resource_group.rg.location
  resource_group_name          = data.azurerm_resource_group.rg.name
  container_app_environment_id = azurerm_container_app_environment.cae[0].id
  replica_timeout_in_seconds   = local.dmd_job_replica_timeout_seconds
  replica_retry_limit          = 0

  manual_trigger_config {
    parallelism              = 1
    replica_completion_count = 1
  }

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.dmd_job_mi[0].id]
  }

  secret {
    name                = "dmd-client-id"
    key_vault_secret_id = "${data.azurerm_key_vault.shared_kv.vault_uri}secrets/dmd-client-id"
    identity            = azurerm_user_assigned_identity.dmd_job_mi[0].id
  }

  secret {
    name                = "dmd-client-secret"
    key_vault_secret_id = "${data.azurerm_key_vault.shared_kv.vault_uri}secrets/dmd-client-secret"
    identity            = azurerm_user_assigned_identity.dmd_job_mi[0].id
  }

  secret {
    name  = "redis-password"
    value = azurerm_redis_cache.redis.primary_access_key
  }

  secret {
    name  = "registry-password"
    value = var.docker_registry_password
  }

  registry {
    server               = split("/", coalesce(var.dmd_cache_job_image, var.docker_image))[0]
    username             = var.docker_registry_username
    password_secret_name = "registry-password"
  }

  template {
    container {
      name   = "dmd-warmer"
      image  = var.dmd_cache_job_image != null ? var.dmd_cache_job_image : ""
      cpu    = 0.5
      memory = "1Gi"

      command = ["/code/.venv/bin/python"]
      args    = ["-m", "app.ops.dmd_cache", "warm"]

      env {
        name  = "DMD_BASE_URL"
        value = var.dmd_base_url
      }
      env {
        name        = "DMD_CLIENT_ID"
        secret_name = "dmd-client-id"
      }
      env {
        name        = "DMD_CLIENT_SECRET"
        secret_name = "dmd-client-secret"
      }
      env {
        name  = "REDIS_HOST"
        value = azurerm_redis_cache.redis.hostname
      }
      env {
        name  = "REDIS_PORT"
        value = tostring(azurerm_redis_cache.redis.ssl_port)
      }
      env {
        name  = "REDIS_SSL"
        value = "true"
      }
      env {
        name        = "REDIS_PASSWORD"
        secret_name = "redis-password"
      }
      env {
        name  = "DMD_LOCK_TTL"
        value = tostring(local.dmd_lock_ttl_seconds)
      }
    }
  }

  lifecycle {
    precondition {
      condition     = var.dmd_cache_job_image != null && can(regex("^ghcr\\.io/uclh-digital-innovation-hub/xhuma@sha256:[0-9a-f]{64}$", var.dmd_cache_job_image))
      error_message = "dmd_cache_job_image is required and must be an immutable sha256 digest (ghcr.io/uclh-digital-innovation-hub/xhuma@sha256:<64 hex chars>) when dmd_cache_warm_enabled is true."
    }
    precondition {
      condition     = local.dmd_job_replica_timeout_seconds < local.dmd_logic_poll_timeout_seconds && local.dmd_logic_poll_timeout_seconds < local.dmd_lock_ttl_seconds
      error_message = "Timeout invariant violated: replica timeout (1800s) must be < poll timeout (2100s) < lock TTL (2700s)."
    }
    ignore_changes = [tags["CostCenter"]]
  }
}

resource "azurerm_logic_app_workflow" "dmd_warm" {
  count               = var.dmd_cache_warm_enabled && var.dmd_cache_schedule_enabled ? 1 : 0
  name                = "${var.app_service_name}-dmd-warm-la"
  location            = data.azurerm_resource_group.rg.location
  resource_group_name = data.azurerm_resource_group.rg.name

  identity {
    type = "SystemAssigned"
  }

  lifecycle {
    precondition {
      condition     = !var.dmd_cache_schedule_enabled
      error_message = "dmd_cache_schedule_enabled remains blocked until a missed-success watchdog is implemented and accepted."
    }
    precondition {
      condition     = var.dmd_cache_warm_enabled
      error_message = "dmd_cache_schedule_enabled requires dmd_cache_warm_enabled to be true."
    }
    precondition {
      condition     = var.dmd_cache_schedule_start_time != null
      error_message = "dmd_cache_schedule_start_time is required when dmd_cache_schedule_enabled is true."
    }
    precondition {
      condition     = length(var.dmd_cache_alert_action_group_ids) > 0
      error_message = "At least one alert action group ID is required in dmd_cache_alert_action_group_ids when dmd_cache_schedule_enabled is true."
    }
    ignore_changes = [tags["CostCenter"]]
  }
}

resource "azurerm_logic_app_trigger_custom" "dmd_warm" {
  count        = var.dmd_cache_warm_enabled && var.dmd_cache_schedule_enabled ? 1 : 0
  name         = "weekly-dmd-warm-recurrence"
  logic_app_id = azurerm_logic_app_workflow.dmd_warm[0].id

  body = jsonencode({
    type = "Recurrence"
    recurrence = {
      frequency = "Week"
      interval  = 1
      timeZone  = "GMT Standard Time"
      startTime = var.dmd_cache_schedule_start_time
      schedule = {
        hours    = [5]
        minutes  = [var.dmd_cache_warm_minute]
        weekDays = ["Monday"]
      }
    }
  })
}

resource "azurerm_logic_app_action_custom" "start_job" {
  count        = var.dmd_cache_warm_enabled && var.dmd_cache_schedule_enabled ? 1 : 0
  name         = "Start_Warmer_Job"
  logic_app_id = azurerm_logic_app_workflow.dmd_warm[0].id

  body = jsonencode({
    type = "Http"
    inputs = {
      method = "POST"
      uri    = "https://management.azure.com/subscriptions/${data.azurerm_client_config.current.subscription_id}/resourceGroups/${data.azurerm_resource_group.rg.name}/providers/Microsoft.App/jobs/${azurerm_container_app_job.dmd_warm[0].name}/start?api-version=2024-03-01"
      headers = {
        "Content-Type" = "application/json"
      }
      body = {}
      authentication = {
        type     = "ManagedServiceIdentity"
        audience = "https://management.azure.com"
      }
    }
  })
}

resource "azurerm_logic_app_action_custom" "poll_async_start" {
  count        = var.dmd_cache_warm_enabled && var.dmd_cache_schedule_enabled ? 1 : 0
  name         = "Poll_Async_Start_If_202"
  logic_app_id = azurerm_logic_app_workflow.dmd_warm[0].id

  body = jsonencode({
    type = "If"
    runAfter = {
      "Start_Warmer_Job" = ["Succeeded"]
    }
    expression = {
      or = [
        {
          equals = [
            "@outputs('Start_Warmer_Job')?['statusCode']",
            202
          ]
        },
        {
          equals = [
            "@empty(body('Start_Warmer_Job')?['name'])",
            true
          ]
        }
      ]
    }
    actions = {
      "Poll_Location_Until_Ready" = {
        type       = "Until"
        expression = "@not(equals(outputs('Get_Async_Operation')?['statusCode'], 202))"
        limit = {
          count   = 20
          timeout = "PT5M"
        }
        actions = {
          "Delay_Async_Poll" = {
            type = "Wait"
            inputs = {
              interval = {
                count = 5
                unit  = "Second"
              }
            }
          }
          "Get_Async_Operation" = {
            type = "Http"
            runAfter = {
              "Delay_Async_Poll" = ["Succeeded"]
            }
            inputs = {
              method = "GET"
              uri    = "@outputs('Start_Warmer_Job')?['headers']?['Location']"
              authentication = {
                type     = "ManagedServiceIdentity"
                audience = "https://management.azure.com"
              }
            }
          }
        }
      }
    }
  })
}

resource "azurerm_logic_app_action_custom" "poll_execution" {
  count        = var.dmd_cache_warm_enabled && var.dmd_cache_schedule_enabled ? 1 : 0
  name         = "Poll_Execution_Until_Terminal"
  logic_app_id = azurerm_logic_app_workflow.dmd_warm[0].id

  body = jsonencode({
    type       = "Until"
    expression = "@or(equals(body('Get_Execution_Status')?['properties']?['status'], 'Succeeded'), equals(body('Get_Execution_Status')?['properties']?['status'], 'Failed'), equals(body('Get_Execution_Status')?['properties']?['status'], 'Stopped'))"
    limit = {
      count   = 70
      timeout = "PT35M"
    }
    runAfter = {
      "Poll_Async_Start_If_202" = ["Succeeded"]
    }
    actions = {
      "Delay_Polling" = {
        type = "Wait"
        inputs = {
          interval = {
            count = 30
            unit  = "Second"
          }
        }
      }
      "Get_Execution_Status" = {
        type = "Http"
        runAfter = {
          "Delay_Polling" = ["Succeeded"]
        }
        inputs = {
          method = "GET"
          uri    = "https://management.azure.com/subscriptions/${data.azurerm_client_config.current.subscription_id}/resourceGroups/${data.azurerm_resource_group.rg.name}/providers/Microsoft.App/jobs/${azurerm_container_app_job.dmd_warm[0].name}/executions/@{coalesce(body('Start_Warmer_Job')?['name'], body('Get_Async_Operation')?['name'], last(split(body('Get_Async_Operation')?['id'], '/')), last(split(outputs('Start_Warmer_Job')?['headers']?['Location'], '/')))}?api-version=2024-03-01"
          authentication = {
            type     = "ManagedServiceIdentity"
            audience = "https://management.azure.com"
          }
        }
      }
    }
  })
}

resource "azurerm_logic_app_action_custom" "check_status" {
  count        = var.dmd_cache_warm_enabled && var.dmd_cache_schedule_enabled ? 1 : 0
  name         = "Check_Job_Terminal_Status"
  logic_app_id = azurerm_logic_app_workflow.dmd_warm[0].id

  body = jsonencode({
    type = "If"
    runAfter = {
      "Poll_Execution_Until_Terminal" = ["Succeeded"]
    }
    expression = {
      and = [
        {
          equals = [
            "@body('Get_Execution_Status')?['properties']?['status']",
            "Succeeded"
          ]
        }
      ]
    }
    actions = {
      "Terminate_Success" = {
        type = "Terminate"
        inputs = {
          runStatus = "Succeeded"
        }
      }
    }
    else = {
      actions = {
        "Terminate_Failure" = {
          type = "Terminate"
          inputs = {
            runStatus = "Failed"
            runError = {
              code    = "DmdWarmExecutionFailed"
              message = "Container Apps Job dmd-warm execution did not terminate with Succeeded status"
            }
          }
        }
      }
    }
  })
}

resource "azurerm_role_definition" "dmd_job_operator" {
  count       = var.dmd_cache_warm_enabled && var.dmd_cache_schedule_enabled ? 1 : 0
  name        = "${var.app_service_name}-dmd-job-operator"
  scope       = data.azurerm_resource_group.rg.id
  description = "Allows starting and polling the dm+d cache warmer job and its async operations"

  permissions {
    actions = [
      "Microsoft.App/jobs/read",
      "Microsoft.App/jobs/start/action",
      "Microsoft.App/jobs/executions/read",
      "Microsoft.App/jobs/execution/read",
      "Microsoft.App/locations/containerappsjoboperationstatuses/read",
      "Microsoft.App/locations/containerappsjoboperationresults/read",
      "Microsoft.App/managedEnvironments/read",
    ]
    not_actions = []
  }

  assignable_scopes = [
    data.azurerm_resource_group.rg.id
  ]
}

resource "azurerm_role_assignment" "logic_app_job_operator" {
  count              = var.dmd_cache_warm_enabled && var.dmd_cache_schedule_enabled ? 1 : 0
  scope              = data.azurerm_resource_group.rg.id
  role_definition_id = azurerm_role_definition.dmd_job_operator[0].role_definition_resource_id
  principal_id       = azurerm_logic_app_workflow.dmd_warm[0].identity[0].principal_id
}

resource "azurerm_monitor_metric_alert" "la_runs_failed" {
  count               = var.dmd_cache_warm_enabled && var.dmd_cache_schedule_enabled ? 1 : 0
  name                = "${var.app_service_name}-dmd-warm-runs-failed"
  resource_group_name = data.azurerm_resource_group.rg.name
  scopes              = [azurerm_logic_app_workflow.dmd_warm[0].id]
  description         = "Alert when dm+d cache warmer Logic App workflow run fails"
  severity            = 1
  frequency           = "PT15M"
  window_size         = "PT1H"

  criteria {
    metric_namespace = "Microsoft.Logic/workflows"
    metric_name      = "RunsFailed"
    aggregation      = "Total"
    operator         = "GreaterThan"
    threshold        = 0
  }

  dynamic "action" {
    for_each = var.dmd_cache_alert_action_group_ids
    content {
      action_group_id = action.value
    }
  }

  lifecycle {
    ignore_changes = [tags["CostCenter"]]
  }
}

resource "azurerm_monitor_metric_alert" "la_triggers_failed" {
  count               = var.dmd_cache_warm_enabled && var.dmd_cache_schedule_enabled ? 1 : 0
  name                = "${var.app_service_name}-dmd-warm-triggers-failed"
  resource_group_name = data.azurerm_resource_group.rg.name
  scopes              = [azurerm_logic_app_workflow.dmd_warm[0].id]
  description         = "Alert when dm+d cache warmer Logic App trigger fails"
  severity            = 1
  frequency           = "PT15M"
  window_size         = "PT1H"

  criteria {
    metric_namespace = "Microsoft.Logic/workflows"
    metric_name      = "TriggersFailed"
    aggregation      = "Total"
    operator         = "GreaterThan"
    threshold        = 0
  }

  dynamic "action" {
    for_each = var.dmd_cache_alert_action_group_ids
    content {
      action_group_id = action.value
    }
  }

  lifecycle {
    ignore_changes = [tags["CostCenter"]]
  }
}

check "dmd_cache_invariants" {
  assert {
    condition     = !var.dmd_cache_schedule_enabled
    error_message = "dmd_cache_schedule_enabled remains blocked until a missed-success watchdog is implemented and accepted."
  }

  assert {
    condition     = !var.dmd_cache_schedule_enabled || var.dmd_cache_warm_enabled
    error_message = "dmd_cache_schedule_enabled requires dmd_cache_warm_enabled to be true."
  }

  assert {
    condition     = !var.dmd_cache_schedule_enabled || var.dmd_cache_schedule_start_time != null
    error_message = "dmd_cache_schedule_start_time is required when dmd_cache_schedule_enabled is true."
  }

  assert {
    condition     = !var.dmd_cache_schedule_enabled || length(var.dmd_cache_alert_action_group_ids) > 0
    error_message = "At least one alert action group ID is required in dmd_cache_alert_action_group_ids when dmd_cache_schedule_enabled is true."
  }

  assert {
    condition     = !var.dmd_cache_warm_enabled || var.dmd_cache_job_image != null
    error_message = "dmd_cache_job_image is required when dmd_cache_warm_enabled is true."
  }

  assert {
    condition     = local.dmd_job_replica_timeout_seconds < local.dmd_logic_poll_timeout_seconds && local.dmd_logic_poll_timeout_seconds < local.dmd_lock_ttl_seconds
    error_message = "Timeout invariant violated: replica timeout (1800s) must be < poll timeout (2100s) < lock TTL (2700s)."
  }
}
