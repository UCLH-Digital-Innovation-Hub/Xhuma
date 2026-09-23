variable "shared_resource_group_name" {
  description = "The name of the shared resource group"
  type        = string
  default     = "rg-xhuma-shared"
}

variable "shared_key_vault_name" {
  description = "The name of the global shared Key Vault"
  type        = string
  default     = "xhuma-shared-kv-int"
}

variable "approved_subnet_ids" {
  description = "Explicit list of allowed App Service integration subnet IDs (e.g., INT, Play, and future trusts)"
  type        = list(string)
}

variable "approved_ip_rules" {
  description = "Explicit list of approved IP rules (e.g., VPNs, GitHub Action Runners)"
  type        = list(string)
  default     = []
}

variable "location" {
  description = "Azure location for the shared Key Vault"
  type        = string
  default     = "UK South"
}

variable "tenant_id" {
  description = "Tenant ID. If blank, uses current client config"
  type        = string
  default     = ""
}

variable "sku_name" {
  description = "SKU Name for the shared Key Vault (standard or premium)"
  type        = string
  default     = "standard"
}

variable "enabled_for_disk_encryption" {
  description = "Whether disk encryption is enabled"
  type        = bool
  default     = true
}

variable "soft_delete_retention_days" {
  description = "Soft delete retention days"
  type        = number
  default     = 7
}

variable "purge_protection_enabled" {
  description = "Whether purge protection is enabled"
  type        = bool
  default     = false
}

variable "public_network_access_enabled" {
  description = "Whether public network access is enabled"
  type        = bool
  default     = true
}
