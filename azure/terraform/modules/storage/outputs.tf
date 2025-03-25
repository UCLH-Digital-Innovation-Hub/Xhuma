# Outputs for the Storage Account module

output "id" {
  description = "The ID of the storage account"
  value       = azurerm_storage_account.storage.id
}

output "name" {
  description = "The name of the storage account"
  value       = azurerm_storage_account.storage.name
}

output "primary_access_key" {
  description = "The primary access key for the storage account"
  value       = azurerm_storage_account.storage.primary_access_key
  sensitive   = true
}

output "primary_connection_string" {
  description = "The primary connection string for the storage account"
  value       = azurerm_storage_account.storage.primary_connection_string
  sensitive   = true
}

output "file_share_ids" {
  description = "Map of file share names to their resource IDs"
  value       = { for name, share in azurerm_storage_share.file_shares : name => share.id }
}

output "file_share_urls" {
  description = "Map of file share names to their URLs"
  value       = { for name, share in azurerm_storage_share.file_shares : name => "https://${azurerm_storage_account.storage.name}.file.core.windows.net/${name}" }
}
