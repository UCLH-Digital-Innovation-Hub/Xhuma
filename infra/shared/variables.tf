# Identify the existing shared vault; its resource group is managed elsewhere.
# The deployment workflow supplies these names from the target's environment.
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

# Firewall inputs are the complete allowlists, not just newly approved entries.
# Reconciliation merges live subnet rules with the target App Service subnet.
variable "approved_subnet_ids" {
  description = "Explicit list of allowed App Service integration subnet IDs (e.g., INT, Play, and future trusts)"
  type        = list(string)
}

# Reconciliation preserves the live IP rules alongside the merged subnet list.
variable "approved_ip_rules" {
  description = "Explicit list of approved IP rules (e.g., VPNs, GitHub Action Runners)"
  type        = list(string)
  default     = []
}

# Vault placement, tenant and service tier must match the adopted resource.
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

# Azure integration capabilities are independent of app/Locust access policies.
variable "enabled_for_disk_encryption" {
  description = "Whether disk encryption is enabled"
  type        = bool
  default     = true
}

variable "enabled_for_deployment" {
  description = "Whether deployment is enabled"
  type        = bool
  default     = true
}

variable "enabled_for_template_deployment" {
  description = "Whether template deployment is enabled"
  type        = bool
  default     = true
}

# Retention and purge protection govern recovery after deletion; prevent_destroy
# in main.tf separately blocks Terraform destruction while configured.
variable "soft_delete_retention_days" {
  description = "Soft delete retention days"
  type        = number
  default     = 90
}

variable "purge_protection_enabled" {
  description = "Whether purge protection is enabled"
  type        = bool
  default     = false
}

# Public endpoint availability is still constrained by main.tf's deny-by-default ACLs.
variable "public_network_access_enabled" {
  description = "Whether public network access is enabled"
  type        = bool
  default     = true
}
