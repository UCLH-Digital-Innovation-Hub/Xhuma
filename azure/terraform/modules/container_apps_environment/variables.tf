# Variables for the Container Apps Environment module

variable "name" {
  description = "The name of the container apps environment"
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

variable "log_analytics_workspace_id" {
  description = "The ID of the Log Analytics workspace to use for monitoring"
  type        = string
}

variable "log_analytics_workspace_key" {
  description = "The primary shared key of the Log Analytics workspace"
  type        = string
  sensitive   = true
  default     = ""
}

variable "infrastructure_subnet_id" {
  description = "The ID of the subnet to use for the container apps environment infrastructure"
  type        = string
  default     = null
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

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}
