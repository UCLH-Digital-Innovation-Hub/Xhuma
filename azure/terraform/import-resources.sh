#!/bin/bash
# Script to import existing Azure resources into Terraform state

# Set variables
RESOURCE_GROUP="rg-xhuma-play"
ENV_NAME="play"  # The base environment name without prefixes

# Get subscription ID
echo "Getting subscription ID..."
SUBSCRIPTION_ID=$(az account show --query id -o tsv)
if [ -z "$SUBSCRIPTION_ID" ]; then
  echo "Error: Could not get subscription ID. Make sure you're logged in to Azure CLI."
  exit 1
fi

echo "Using subscription: $SUBSCRIPTION_ID"
echo "Using resource group: $RESOURCE_GROUP"
echo "Using environment name: $ENV_NAME"

# Initialize Terraform
echo "Initializing Terraform..."
terraform init

# Import ACR
echo "Importing Container Registry..."
terraform import module.acr.azurerm_container_registry.acr \
  /subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RESOURCE_GROUP}/providers/Microsoft.ContainerRegistry/registries/crxhuma${ENV_NAME}

# Import Storage Account
echo "Importing Storage Account..."
terraform import module.storage.azurerm_storage_account.storage \
  /subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RESOURCE_GROUP}/providers/Microsoft.Storage/storageAccounts/st${ENV_NAME}xhuma

# Import Key Vault
echo "Importing Key Vault..."
terraform import module.key_vault.azurerm_key_vault.key_vault \
  /subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RESOURCE_GROUP}/providers/Microsoft.KeyVault/vaults/kvxhuma${ENV_NAME}

# Import Log Analytics Workspace
echo "Importing Log Analytics Workspace..."
terraform import module.log_analytics.azurerm_log_analytics_workspace.workspace \
  /subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RESOURCE_GROUP}/providers/Microsoft.OperationalInsights/workspaces/logxhuma${ENV_NAME}

echo "Import completed. Now you can run terraform plan to verify state."

# Get ACR credentials (optional - uncomment if needed)
echo "Getting ACR credentials..."
ACR_NAME="crxhuma${ENV_NAME}"
ACR_LOGIN_SERVER=$(az acr show --name $ACR_NAME --resource-group $RESOURCE_GROUP --query loginServer -o tsv)
ACR_CREDS=$(az acr credential show --name $ACR_NAME --resource-group $RESOURCE_GROUP)

# Extract username and password
ACR_USERNAME=$(echo $ACR_CREDS | jq -r '.username')
ACR_PASSWORD=$(echo $ACR_CREDS | jq -r '.passwords[0].value')

echo "ACR Login Server: $ACR_LOGIN_SERVER"
echo "ACR Username: $ACR_USERNAME"
echo "ACR Password: [HIDDEN]"
echo ""
echo "Add these values as GitHub secrets:"
echo "ACR_LOGIN_SERVER: $ACR_LOGIN_SERVER"
echo "ACR_USERNAME: $ACR_USERNAME"
echo "ACR_PASSWORD: [Use the value above]"
