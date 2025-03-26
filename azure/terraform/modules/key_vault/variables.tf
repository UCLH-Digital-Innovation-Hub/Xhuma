# Variables for the Key Vault module

variable "name" {
  description = "The name of the key vault"
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

variable "tenant_id" {
  description = "The Azure AD tenant ID"
  type        = string
}

variable "current_object_id" {
  description = "The object ID of the current user/service principal"
  type        = string
  default     = ""
}

variable "sku_name" {
  description = "The SKU of the key vault"
  type        = string
  default     = "standard"
}

variable "enabled_for_disk_encryption" {
  description = "Whether key vault is enabled for disk encryption"
  type        = bool
  default     = true
}

variable "purge_protection_enabled" {
  description = "Whether purge protection is enabled"
  type        = bool
  default     = false
}

variable "soft_delete_retention_days" {
  description = "The number of days that deleted keys should be retained"
  type        = number
  default     = 7
}

variable "secrets" {
  description = "Map of secrets to add to the key vault"
  type        = map(string)
  default     = {}
  sensitive   = true
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}
