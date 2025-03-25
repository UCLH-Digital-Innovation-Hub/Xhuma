# Azure Key Vault module

resource "azurerm_key_vault" "key_vault" {
  name                        = var.name
  resource_group_name         = var.resource_group_name
  location                    = var.location
  tenant_id                   = var.tenant_id
  sku_name                    = var.sku_name
  tags                        = var.tags
  enabled_for_disk_encryption = var.enabled_for_disk_encryption
  purge_protection_enabled    = var.purge_protection_enabled
  soft_delete_retention_days  = var.soft_delete_retention_days
  
  # Default access policy for the current client
  access_policy {
    tenant_id = var.tenant_id
    object_id = var.current_object_id
    
    key_permissions = [
      "Get", "List", "Create", "Delete", "Update",
    ]
    
    secret_permissions = [
      "Get", "List", "Set", "Delete",
    ]
    
    certificate_permissions = [
      "Get", "List", "Create", "Delete",
    ]
  }
}

# Add secrets if provided
resource "azurerm_key_vault_secret" "secrets" {
  for_each     = var.secrets
  name         = each.key
  value        = each.value
  key_vault_id = azurerm_key_vault.key_vault.id
}
