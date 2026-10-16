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

variable "shared_subscription_id" {
  description = "The subscription ID hosting the shared Key Vault"
  type        = string
}

variable "shared_key_vault_name" {
  description = "The name of the global shared Key Vault"
  type        = string
}

variable "app_version" {
  type    = string
  default = "0.9.2"
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

variable "xhuma_ods_code" {
  description = "ODS code identifying the Xhuma/supplier organisation in SDS"
  type        = string
}

variable "xhuma_mhs_party_key" {
  description = "MHS Party Key identifying Xhuma's consumer Device in SDS"
  type        = string
  default     = ""
}

variable "env" {
  description = "Environment Name"
  type        = string
  default     = "prod"
}

variable "ccda_expiry_hours" {
  description = "Number of hours to cache CCDA documents"
  type        = string
  default     = "4"
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

variable "nhs_relay_base_path" {
  description = "The base path for the NHS GP Connect API over HSCN/Relay"
  type        = string
  default     = ""
}

variable "nhs_over_internet_path" {
  description = "The base path for the NHS GP Connect API over the internet"
  type        = string
  default     = ""
}

variable "dmd_base_url" {
  description = "The FHIR Terminology base URL for DMD"
  type        = string
  default     = "https://ontology.nhs.uk/production1/fhir"
}

variable "dmd_cache_warm_enabled" {
  description = "Enable target-local dm+d cache warmer infrastructure (Container Apps Environment, Job, Managed Identity)"
  type        = bool
  default     = false
}

variable "dmd_cache_schedule_enabled" {
  description = "Enable scheduled dm+d cache warmer Logic App workflow (Monday 05:00 UK time recurrence)"
  type        = bool
  default     = false
}

variable "dmd_cache_warm_minute" {
  description = "Minute of the 05:00 hour for scheduled dm+d warming recurrence (0-59)"
  type        = number
  default     = 0

  validation {
    condition     = var.dmd_cache_warm_minute >= 0 && var.dmd_cache_warm_minute <= 59
    error_message = "dmd_cache_warm_minute must be between 0 and 59 inclusive."
  }
}

variable "dmd_cache_job_image" {
  description = "Fully-qualified digest-pinned container image for dm+d cache warmer job (e.g. ghcr.io/uclh-digital-innovation-hub/xhuma@sha256:<64 hex chars>)"
  type        = string
  default     = null

  validation {
    condition     = var.dmd_cache_job_image == null || can(regex("^ghcr\\.io/uclh-digital-innovation-hub/xhuma@sha256:[0-9a-f]{64}$", var.dmd_cache_job_image))
    error_message = "dmd_cache_job_image must be fully qualified and pinned to an immutable sha256 digest: ghcr.io/uclh-digital-innovation-hub/xhuma@sha256:<64 hex chars>"
  }
}

variable "dmd_cache_schedule_start_time" {
  description = "Target-specific future start time for weekly recurrence in local format without Z: YYYY-MM-DDTHH:MM:SS"
  type        = string
  default     = null

  validation {
    condition     = var.dmd_cache_schedule_start_time == null || can(regex("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}$", var.dmd_cache_schedule_start_time))
    error_message = "dmd_cache_schedule_start_time must be in local format without Z: YYYY-MM-DDTHH:MM:SS"
  }
}

variable "dmd_cache_alert_action_group_ids" {
  description = "List of Azure Monitor Action Group resource IDs for dm+d cache warmer alerting. Required when dmd_cache_schedule_enabled is true."
  type        = list(string)
  default     = []
}
