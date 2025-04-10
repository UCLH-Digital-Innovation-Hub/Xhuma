# Azure Container Apps module for Xhuma application

# Xhuma main application container app
resource "azurerm_container_app" "xhuma" {
  name                         = "ca-xhuma-${local.env_base_name}"
  container_app_environment_id = var.container_app_environment_id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"
  tags                         = var.tags
  
  # Temporarily removed prevent_destroy to allow recreation
  # lifecycle {
  #   prevent_destroy = true
  # }

  template {
    container {
      name   = "xhuma"
      image  = "${var.acr_login_server}/xhuma:latest"
      cpu    = 0.5
      memory = "1Gi"

      env {
        name  = "API_KEY"
        value = var.api_key
      }
      
      env {
        name  = "OTEL_EXPORTER_OTLP_ENDPOINT"
        value = "http://ca-otel-collector-${local.env_base_name}.internal:4317"
      }
      
      env {
        name  = "OTEL_PYTHON_METER_PROVIDER"
        value = "sdk_meter_provider"
      }
      
      env {
        name  = "OTEL_PYTHON_TRACER_PROVIDER"
        value = "sdk_tracer_provider"
      }
      
      env {
        name  = "OTEL_SERVICE_NAME"
        value = "xhuma"
      }
      
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
      
      env {
        name  = "POSTGRES_HOST"
        value = "ca-postgres-${local.env_base_name}.internal"
      }
      
      env {
        name  = "REDIS_HOST"
        value = "ca-redis-${local.env_base_name}.internal"
      }
      
      env {
        name  = "REDIS_PASSWORD"
        value = var.redis_password
      }
    }

    min_replicas = 1
    max_replicas = 10
  }
  
  ingress {
    external_enabled = true
    target_port      = 80
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }
  
  # Use secret-based authentication for ACR
  secret {
    name  = "registry-password"
    value = var.acr_admin_password
  }
  
  # Reference the container registry with credentials
  registry {
    server               = var.acr_login_server
    username             = var.acr_admin_username
    password_secret_name = "registry-password"
  }
}

# Redis container app
resource "azurerm_container_app" "redis" {
  name                         = "ca-redis-${local.env_base_name}"
  container_app_environment_id = var.container_app_environment_id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"
  tags                         = var.tags
  
  # Temporarily removed prevent_destroy to allow recreation
  # lifecycle {
  #   prevent_destroy = true
  # }

  template {
    container {
      name   = "redis"
      image  = "redis:7.2"
      cpu    = 0.5
      memory = "1Gi"
      
      command = [
        "/bin/sh",
        "-c",
        "redis-server --requirepass \"${var.redis_password}\" --maxmemory 256mb --maxmemory-policy volatile-lru --appendonly yes --appendfsync everysec --save 900 1 --save 300 10 --save 60 10000 --maxclients 100 --timeout 300 --tcp-keepalive 60"
      ]
      
      env {
        name  = "REDIS_PASSWORD"
        value = var.redis_password
      }
      
      volume_mounts {
        name = "redis-data"
        path = "/data"
      }
    }
    
    min_replicas = 1
    max_replicas = 1
    
    volume {
      name         = "redis-data"
      storage_type = "AzureFile"
      storage_name = "redis-data"
    }
  }
  
  ingress {
    external_enabled = false
    target_port      = 6379
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }
}

# PostgreSQL container app
resource "azurerm_container_app" "postgres" {
  name                         = "ca-postgres-${local.env_base_name}"
  container_app_environment_id = var.container_app_environment_id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"
  tags                         = var.tags
  
  # Temporarily removed prevent_destroy to allow recreation
  # lifecycle {
  #   prevent_destroy = true
  # }

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
      
      volume_mounts {
        name = "postgres-data"
        path = "/var/lib/postgresql/data"
      }
    }
    
    min_replicas = 1
    max_replicas = 1
    
    volume {
      name         = "postgres-data"
      storage_type = "AzureFile"
      storage_name = "postgres-data"
    }
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

# Prometheus container app
resource "azurerm_container_app" "prometheus" {
  name                         = "ca-prometheus-${local.env_base_name}"
  container_app_environment_id = var.container_app_environment_id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"
  tags                         = var.tags

  template {
    container {
      name   = "prometheus"
      image  = "prom/prometheus:v2.45.0"
      cpu    = 0.5
      memory = "1Gi"
      
      command = [
        "--config.file=/etc/prometheus/prometheus.yml",
        "--storage.tsdb.path=/prometheus",
        "--web.console.libraries=/usr/share/prometheus/console_libraries",
        "--web.console.templates=/usr/share/prometheus/consoles"
      ]
    }
    
    min_replicas = 1
    max_replicas = 1
  }
  
  ingress {
    external_enabled = false
    target_port      = 9090
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }
}

# Grafana container app
resource "azurerm_container_app" "grafana" {
  name                         = "ca-grafana-${local.env_base_name}"
  container_app_environment_id = var.container_app_environment_id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"
  tags                         = var.tags

  template {
    container {
      name   = "grafana"
      image  = "grafana/grafana:10.0.3"
      cpu    = 0.5
      memory = "1Gi"
      
      env {
        name  = "GF_SECURITY_ADMIN_PASSWORD"
        value = var.grafana_admin_password
      }
      
      env {
        name  = "GF_USERS_ALLOW_SIGN_UP"
        value = "false"
      }
    }
    
    min_replicas = 1
    max_replicas = 1
  }
  
  ingress {
    external_enabled = false
    target_port      = 3000
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }
}

# OpenTelemetry collector container app
resource "azurerm_container_app" "otel_collector" {
  name                         = "ca-otel-collector-${local.env_base_name}"
  container_app_environment_id = var.container_app_environment_id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"
  tags                         = var.tags

  template {
    container {
      name   = "otel-collector"
      image  = "otel/opentelemetry-collector:0.85.0"
      cpu    = 0.5
      memory = "1Gi"
      
      command = ["--config=/etc/otel-collector-config.yaml"]
    }
    
    min_replicas = 1
    max_replicas = 1
  }
  
  ingress {
    external_enabled = false
    target_port      = 4317
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }
}

# Tempo container app
resource "azurerm_container_app" "tempo" {
  name                         = "ca-tempo-${local.env_base_name}"
  container_app_environment_id = var.container_app_environment_id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"
  tags                         = var.tags

  template {
    container {
      name   = "tempo"
      image  = "grafana/tempo:latest"
      cpu    = 0.5
      memory = "1Gi"
      
      command = ["-config.file=/etc/tempo.yaml"]
    }
    
    min_replicas = 1
    max_replicas = 1
  }
  
  ingress {
    external_enabled = false
    target_port      = 3200
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }
}
