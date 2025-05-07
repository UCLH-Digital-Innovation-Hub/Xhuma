# Variables for Xhuma Azure Infrastructure

variable "resource_group_name" {
  description = "Name of the existing resource group (e.g., rg-xhuma-play)"
  type        = string
  default     = "rg-xhuma-play"
}

variable "location" {
  description = "Azure region where resources will be created"
  type        = string
  default     = "uksouth"
}

variable "redis_password" {
  description = "Password for Azure Cache for Redis"
  type        = string
  sensitive   = true
}

variable "postgres_password" {
  description = "Password for PostgreSQL container"
  type        = string
  sensitive   = true
  default     = ""  # Optional: Provide if deploying PostgreSQL
}

variable "api_key" {
  description = "API key for NHS Digital services"
  type        = string
  sensitive   = true
}

variable "jwtkey" {
  description = "Private key for JWT signing (in PEM format). This is required and must be provided securely via environment variable TF_VAR_jwtkey or through a .tfvars file that is excluded from version control."
  type        = string
  sensitive   = true
  
  validation {
    condition     = length(var.jwtkey) > 0
    error_message = "The jwtkey variable must be provided for secure JWT signing. It should contain a PEM-formatted RSA private key."
  }
}

variable "grafana_admin_password" {
  description = "Admin password for Grafana"
  type        = string
  sensitive   = true
  default     = "admin"  # Default value, should be overridden in production
}

variable "image_tag" {
  description = "Tag for the Docker images (default is latest, but should be GitHash in CD)"
  type        = string
  default     = "latest"
}

variable "enable_postgres" {
  description = "Flag to enable/disable PostgreSQL deployment"
  type        = bool
  default     = true
}

variable "enable_observability" {
  description = "Flag to enable/disable observability stack (Prometheus, Grafana, etc.)"
  type        = bool
  default     = true
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
