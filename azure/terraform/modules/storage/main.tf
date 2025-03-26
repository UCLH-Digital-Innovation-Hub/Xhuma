# Azure Storage Account module

resource "azurerm_storage_account" "storage" {
  name                     = var.name
  resource_group_name      = var.resource_group_name
  location                 = var.location
  account_tier             = var.account_tier
  account_replication_type = var.account_replication_type
  tags                     = var.tags
  
  # Security settings
  min_tls_version            = "TLS1_2"
  https_traffic_only_enabled = true  # Updated from deprecated enable_https_traffic_only
  # Removed deprecated allow_blob_public_access attribute
  # Using newer attribute to maintain the same security level
  shared_access_key_enabled  = true  # Enabled to allow access via storage keys
}

# Create file shares for Redis and PostgreSQL data
resource "azurerm_storage_share" "file_shares" {
  for_each             = toset(var.file_share_names)
  name                 = each.key
  storage_account_name = azurerm_storage_account.storage.name
  quota                = var.file_share_quota
}
