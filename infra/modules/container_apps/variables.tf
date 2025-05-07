variable "resource_group_name" {
  description = "Name of the resource group"
  type        = string
}

variable "location" {
  description = "Azure region where resources will be created"
  type        = string
}

variable "env_base_name" {
  description = "Base name for environment (used in resource naming)"
  type        = string
}

variable "container_app_environment_id" {
  description = "ID of the Container App Environment"
  type        = string
}

variable "acr_login_server" {
  description = "Login server URL for the Azure Container Registry"
  type        = string
}

variable "acr_admin_username" {
  description = "Admin username for the Azure Container Registry"
  type        = string
}

variable "acr_admin_password" {
  description = "Admin password for the Azure Container Registry"
  type        = string
  sensitive   = true
}

variable "redis_host" {
  description = "Hostname of the Redis instance"
  type        = string
}

variable "redis_password" {
  description = "Password for Redis"
  type        = string
  sensitive   = true
}

variable "postgres_password" {
  description = "Password for PostgreSQL"
  type        = string
  sensitive   = true
  default     = ""
}

variable "api_key" {
  description = "API key for NHS Digital services"
  type        = string
  sensitive   = true
}

variable "jwtkey" {
  description = "Private key for JWT signing (in PEM format)"
  type        = string
  sensitive   = true
}

variable "grafana_admin_password" {
  description = "Admin password for Grafana"
  type        = string
  sensitive   = true
  default     = "admin"
}

variable "image_tag" {
  description = "Tag for the Docker image"
  type        = string
  default     = "latest"
}

variable "min_replicas" {
  description = "Minimum number of replicas for the Xhuma app"
  type        = number
  default     = 1
}

variable "max_replicas" {
  description = "Maximum number of replicas for the Xhuma app"
  type        = number
  default     = 10
}

variable "enable_postgres" {
  description = "Flag to enable/disable PostgreSQL deployment"
  type        = bool
  default     = true
}

variable "enable_observability" {
  description = "Flag to enable/disable observability stack"
  type        = bool
  default     = false
}

variable "tags" {
  description = "Tags to apply to resources"
  type        = map(string)
  default     = {}
}
