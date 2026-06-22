variable "resource_group_name" {
  description = "The name of the resource group"
  type        = string
}

variable "location" {
  description = "The Azure location for resources"
  type        = string
  default     = "UK South"
}

variable "app_service_name" {
  description = "Name of the Azure Web App"
  type        = string
}

variable "redis_name" {
  description = "Name of the Redis Cache instance"
  type        = string
}

variable "redis_sku_name" {
  description = "The SKU of Redis to use. Possible values are Basic, Standard and Premium."
  type        = string
  default     = "Standard"
}

variable "redis_family" {
  description = "The SKU family to use. C = Basic/Standard, P = Premium."
  type        = string
  default     = "C"
}

variable "redis_capacity" {
  description = "The size of the Redis cache to deploy. Valid values for a SKU family of C (Basic/Standard) are 0, 1, 2, 3, 4, 5, 6."
  type        = number
  default     = 1
}

variable "postgres_server_name" {
  description = "Name of the Postgres Flexible Server"
  type        = string
}

variable "postgres_admin_username" {
  description = "Postgres admin username"
  type        = string
  default     = "xhumaadmin"
}

variable "postgres_admin_password" {
  description = "Postgres admin password"
  type        = string
  sensitive   = true
}

variable "shared_resource_group_name" {
  description = "The name of the shared resource group"
  type        = string
}

variable "shared_key_vault_name" {
  description = "The name of the global shared Key Vault"
  type        = string
}

variable "app_version" {
  type    = string
  default = "0.9"
}

variable "device_id" {
  type    = string
  default = "1"
}

variable "org_asid" {
  type    = string
  default = ""
}

variable "gp_connect_include_allergies" {
  type    = string
  default = "true"
}

variable "gp_connect_include_medication" {
  type    = string
  default = "true"
}

variable "gp_connect_include_problems" {
  type    = string
  default = "true"
}

variable "gp_connect_include_investigations" {
  type    = string
  default = "true"
}

variable "gp_connect_include_immunisations" {
  type    = string
  default = "true"
}

variable "registry_id" {
  description = "Registry ID"
  type        = string
}

variable "docker_image" {
  description = "Docker image to deploy"
  type        = string
  default     = "ghcr.io/uclh-digital-innovation-hub/xhuma:latest"
}

variable "docker_registry_url" {
  description = "Docker registry URL"
  type        = string
  default     = "https://ghcr.io"
}

variable "docker_registry_username" {
  description = "Docker registry username"
  type        = string
}

variable "docker_registry_password" {
  description = "Docker registry password"
  type        = string
  sensitive   = true
}

variable "org_code" {
  description = "Organization Code"
  type        = string
  default     = "RRV00"
}

variable "env" {
  description = "Environment Name"
  type        = string
  default     = "prod"
}

variable "otel_metric_export_interval_ms" {
  description = "OpenTelemetry Metric Export Interval (ms)"
  type        = string
  default     = "5000"
}

variable "cors_origins" {
  description = "Comma separated list of allowed CORS origins"
  type        = string
  default     = "*" # Default to * for dev, override in prod
}

variable "allowed_hosts" {
  description = "Comma separated list of allowed hosts"
  type        = string
  default     = "*" # Default to * for dev, override in prod
}

variable "require_mtls" {
  description = "Whether to enforce mTLS globally in the FastAPI app"
  type        = string
  default     = "true"
}

variable "external_relay_url" {
  description = "The URL of the external Shared Relay Server"
  type        = string
  default     = ""
}

variable "external_relay_client_id" {
  description = "The Client ID to route through on the Shared Relay Server"
  type        = string
  default     = "client1"
}

