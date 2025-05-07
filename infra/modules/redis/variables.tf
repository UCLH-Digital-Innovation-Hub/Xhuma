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

variable "redis_password" {
  description = "Password for Redis (will be stored in Key Vault when available)"
  type        = string
  sensitive   = true
}

variable "tags" {
  description = "Tags to apply to resources"
  type        = map(string)
  default     = {}
}

# Optional: Use when Key Vault is available
# variable "key_vault_id" {
#   description = "ID of the Key Vault where Redis password will be stored"
#   type        = string
#   default     = ""
# }
