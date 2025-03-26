# Outputs for the Key Vault module

output "id" {
  description = "The ID of the key vault"
  value       = azurerm_key_vault.key_vault.id
}

output "name" {
  description = "The name of the key vault"
  value       = azurerm_key_vault.key_vault.name
}

output "vault_uri" {
  description = "The URI of the key vault"
  value       = azurerm_key_vault.key_vault.vault_uri
}

output "secrets" {
  description = "Map of secret names to their versions"
  value       = { for k, v in azurerm_key_vault_secret.secrets : k => v.version }
  sensitive   = false
}
