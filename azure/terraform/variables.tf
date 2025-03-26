# Variables for Xhuma Azure deployment

variable "environment" {
  description = "The environment name (e.g., play, dev, test, prod)"
  type        = string
  default     = "play"
}

variable "location" {
  description = "The Azure region where resources will be created"
  type        = string
  default     = "uksouth"
}

variable "resource_group_name" {
  description = "The name of the resource group to use (will be derived if not provided)"
  type        = string
  default     = ""
}

# Tag-related variables according to UCL requirements
variable "created_by" {
  description = "Person/Project Team creating resources"
  type        = string
  default     = "Xhuma Team"
}

variable "git_origin" {
  description = "Source repository URL"
  type        = string
  default     = "https://github.com/UCLH-Digital-Innovation-Hub/Xhuma"
}

variable "owner" {
  description = "Person responsible for the project"
  type        = string
  default     = "Xhuma Team"
}

variable "service_hours" {
  description = "Reference to SLA for project"
  type        = string
  default     = "none"
}

# Secrets for the application
variable "redis_password" {
  description = "Password for Redis"
  type        = string
  sensitive   = true
  default     = ""
}

variable "postgres_password" {
  description = "Password for PostgreSQL"
  type        = string
  sensitive   = true
  default     = ""
}

variable "api_key" {
  description = "API key for Xhuma"
  type        = string
  sensitive   = true
  default     = ""
}

variable "grafana_admin_password" {
  description = "Admin password for Grafana"
  type        = string
  sensitive   = true
  default     = ""
}
