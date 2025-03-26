# Variables for the Container Apps module

variable "environment" {
  description = "The environment name (e.g., play, dev, test, prod)"
  type        = string
}

variable "resource_group_name" {
  description = "The name of the resource group"
  type        = string
}

variable "location" {
  description = "The Azure region where resources will be created"
  type        = string
}

variable "container_app_environment_id" {
  description = "The ID of the container app environment"
  type        = string
}

variable "acr_login_server" {
  description = "The login server URL for the container registry"
  type        = string
}

variable "acr_admin_username" {
  description = "The admin username for the container registry"
  type        = string
}

variable "acr_admin_password" {
  description = "The admin password for the container registry"
  type        = string
  sensitive   = true
}

variable "storage_account_name" {
  description = "The name of the storage account for file shares"
  type        = string
}

variable "storage_account_key" {
  description = "The access key for the storage account"
  type        = string
  sensitive   = true
}

variable "key_vault_id" {
  description = "The ID of the key vault"
  type        = string
  default     = ""
}

# Application secrets
variable "redis_password" {
  description = "Password for Redis"
  type        = string
  sensitive   = true
}

variable "postgres_password" {
  description = "Password for PostgreSQL"
  type        = string
  sensitive   = true
}

variable "api_key" {
  description = "API key for Xhuma"
  type        = string
  sensitive   = true
}

variable "grafana_admin_password" {
  description = "Admin password for Grafana"
  type        = string
  sensitive   = true
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}
