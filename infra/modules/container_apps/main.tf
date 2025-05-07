# Azure Container Apps Module for Xhuma

locals {
  env_name = var.env_base_name
}

# Xhuma main application container app
resource "azurerm_container_app" "xhuma" {
  name                         = "ca-xhuma-${local.env_name}"
  container_app_environment_id = var.container_app_environment_id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"
  tags                         = var.tags

  lifecycle {
    # Don't destroy in production
    prevent_destroy = false  # Set to true for production environments
    
    # Allow image updates without recreating the app
    ignore_changes = [
      template[0].container[0].image,
      tags
    ]
  }

  template {
    container {
      name   = "xhuma"
      image  = "${var.acr_login_server}/xhuma:${var.image_tag}"
      cpu    = 0.5
      memory = "1Gi"

      env {
        name  = "API_KEY"
        value = var.api_key
      }

      env {
        name  = "REDIS_HOST"
        value = var.redis_host
      }

      env {
        name  = "REDIS_PASSWORD"
        value = var.redis_password
      }

      # Health probe for application monitoring
      liveness_probe {
        path                = "/health"
        port                = 80
        initial_delay       = 30
        interval_seconds    = 10
        timeout             = 3
        failure_threshold   = 3
        success_threshold   = 1
      }
    }

    min_replicas = var.min_replicas
    max_replicas = var.max_replicas
  }

  ingress {
    external_enabled = true
    target_port      = 80
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
    # HTTP is currently enabled. For HTTPS, add the following commented section:
    # transport = "http" # Options are: auto, http, http2
  }

  # ACR authentication
  registry {
    server               = var.acr_login_server
    username             = var.acr_admin_username
    password_secret_name = "acr-password"
  }

  secret {
    name  = "acr-password"
    value = var.acr_admin_password
  }

  secret {
    name  = "redis-password"
    value = var.redis_password
  }
}

# Optional: Postgres container app - conditionally created based on var.enable_postgres
resource "azurerm_container_app" "postgres" {
  count                        = var.enable_postgres ? 1 : 0
  name                         = "ca-postgres-${local.env_name}"
  container_app_environment_id = var.container_app_environment_id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"
  tags                         = var.tags

  lifecycle {
    prevent_destroy = false  # Set to true for production environments
  }

  template {
    container {
      name   = "postgres"
      image  = "postgres:15"
      cpu    = 1.0
      memory = "2Gi"

      env {
        name  = "POSTGRES_DB"
        value = "xhuma"
      }

      env {
        name  = "POSTGRES_USER"
        value = "postgres"
      }

      env {
        name  = "POSTGRES_PASSWORD"
        value = var.postgres_password
      }

      # You may add volume mounts here for persistent storage
      # volume_mounts {
      #   name = "postgres-data"
      #   path = "/var/lib/postgresql/data"
      # }
    }

    min_replicas = 1
    max_replicas = 1

    # For persistent storage, uncomment and configure:
    # volume {
    #   name         = "postgres-data"
    #   storage_type = "AzureFile"
    #   storage_name = "postgres-data"
    # }
  }

  ingress {
    external_enabled = false
    target_port      = 5432
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }
}

# Observability-related resources - conditionally created
# These are commented out for now, as specified in the requirements
# They can be uncommented and configured in a future iteration

# resource "azurerm_container_app" "prometheus" {
#   count                        = var.enable_observability ? 1 : 0
#   name                         = "ca-prometheus-${local.env_name}"
#   container_app_environment_id = var.container_app_environment_id
#   resource_group_name          = var.resource_group_name
#   revision_mode                = "Single"
#   tags                         = var.tags
#
#   # Configuration for Prometheus container...
# }
# 
# resource "azurerm_container_app" "grafana" {
#   count                        = var.enable_observability ? 1 : 0
#   name                         = "ca-grafana-${local.env_name}"
#   container_app_environment_id = var.container_app_environment_id
#   resource_group_name          = var.resource_group_name
#   revision_mode                = "Single"
#   tags                         = var.tags
#
#   # Configuration for Grafana container...
# }
#
# resource "azurerm_container_app" "otel_collector" {
#   count                        = var.enable_observability ? 1 : 0
#   name                         = "ca-otel-collector-${local.env_name}"
#   container_app_environment_id = var.container_app_environment_id
#   resource_group_name          = var.resource_group_name
#   revision_mode                = "Single"
#   tags                         = var.tags
#
#   # Configuration for OpenTelemetry collector...
# }
