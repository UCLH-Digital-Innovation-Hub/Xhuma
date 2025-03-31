#!/bin/bash
# Script to import existing Azure resources into Terraform state

# Default values
RESOURCE_GROUP="rg-xhuma-play"
ENV_NAME=""

# Parse command line arguments
while getopts ":g:e:" opt; do
  case $opt in
    g) RESOURCE_GROUP="$OPTARG" ;;
    e) ENV_NAME="$OPTARG" ;;
    \?) echo "Invalid option -$OPTARG" >&2; exit 1 ;;
  esac
done

# Extract environment name from resource group if not provided
if [ -z "$ENV_NAME" ]; then
  # If environment is like "rg-xhuma-play", extract just "play"
  ENV_NAME=$(echo $RESOURCE_GROUP | sed -E 's/rg-xhuma-//g' | sed -E 's/rg-//g')
fi

# Get subscription ID
echo "Getting subscription ID..."
SUBSCRIPTION_ID=$(az account show --query id -o tsv)
if [ -z "$SUBSCRIPTION_ID" ]; then
  echo "Error: Could not get subscription ID. Make sure you're logged in to Azure CLI."
  exit 1
fi

# Check if resource group exists
echo "Checking if resource group exists..."
RESOURCE_GROUP_EXISTS=$(az group exists --name $RESOURCE_GROUP --query bool -o tsv)
if [ "$RESOURCE_GROUP_EXISTS" != "true" ]; then
  echo "Error: Resource group $RESOURCE_GROUP does not exist."
  exit 1
fi

echo "Using subscription: $SUBSCRIPTION_ID"
echo "Using resource group: $RESOURCE_GROUP"
echo "Using environment name: $ENV_NAME"

# Initialize Terraform
echo "Initializing Terraform..."
terraform init

# Function to import a resource if it exists
import_if_exists() {
  local resource_type=$1
  local terraform_address=$2
  local azure_resource_id=$3
  
  echo "Checking if $resource_type exists..."
  if az resource show --ids "$azure_resource_id" &>/dev/null; then
    echo "Importing $resource_type..."
    terraform import "$terraform_address" "$azure_resource_id"
    return 0
  else
    echo "Resource $resource_type doesn't exist, skipping import."
    return 1
  fi
}

# ACR resource id
ACR_ID="/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RESOURCE_GROUP}/providers/Microsoft.ContainerRegistry/registries/crxhuma${ENV_NAME}"
# Storage Account resource id
STORAGE_ID="/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RESOURCE_GROUP}/providers/Microsoft.Storage/storageAccounts/st${ENV_NAME}xhuma"
# Key Vault resource id
KEYVAULT_ID="/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RESOURCE_GROUP}/providers/Microsoft.KeyVault/vaults/kvxhuma${ENV_NAME}01"
# Log Analytics Workspace resource id
LOG_ANALYTICS_ID="/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RESOURCE_GROUP}/providers/Microsoft.OperationalInsights/workspaces/logxhuma${ENV_NAME}"
# Container Apps Environment resource id
CONTAINER_APPS_ENV_ID="/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RESOURCE_GROUP}/providers/Microsoft.App/managedEnvironments/caexhuma${ENV_NAME}"

# Import resources
import_if_exists "Container Registry" "module.acr.azurerm_container_registry.acr" "$ACR_ID"
import_if_exists "Storage Account" "module.storage.azurerm_storage_account.storage" "$STORAGE_ID"
import_if_exists "Key Vault" "module.key_vault.azurerm_key_vault.key_vault" "$KEYVAULT_ID"
import_if_exists "Log Analytics Workspace" "module.log_analytics.azurerm_log_analytics_workspace.workspace" "$LOG_ANALYTICS_ID"
import_if_exists "Container Apps Environment" "module.container_apps_environment.azurerm_container_app_environment.environment" "$CONTAINER_APPS_ENV_ID"

# Import container apps if the environment exists
if import_if_exists "Container Apps Environment" "module.container_apps_environment.azurerm_container_app_environment.environment" "$CONTAINER_APPS_ENV_ID"; then
  # Try to import container apps
  echo "Checking for container apps..."
  CONTAINER_APPS=$(az containerapp list --resource-group $RESOURCE_GROUP --query "[].name" -o tsv)
  
  if [ -n "$CONTAINER_APPS" ]; then
    echo "Found container apps: $CONTAINER_APPS"
    
    # Import each container app by checking name patterns
    for APP in $CONTAINER_APPS; do
      APP_ID="/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RESOURCE_GROUP}/providers/Microsoft.App/containerApps/${APP}"
      
      if [[ "$APP" == "ca-xhuma-"* ]]; then
        echo "Importing Xhuma container app..."
        terraform import "module.container_apps.azurerm_container_app.xhuma" "$APP_ID"
      elif [[ "$APP" == "ca-redis-"* ]]; then
        echo "Importing Redis container app..."
        terraform import "module.container_apps.azurerm_container_app.redis" "$APP_ID"
      elif [[ "$APP" == "ca-postgres-"* ]]; then
        echo "Importing PostgreSQL container app..."
        terraform import "module.container_apps.azurerm_container_app.postgres" "$APP_ID"
      elif [[ "$APP" == "ca-prometheus-"* ]]; then
        echo "Importing Prometheus container app..."
        terraform import "module.container_apps.azurerm_container_app.prometheus" "$APP_ID"
      elif [[ "$APP" == "ca-grafana-"* ]]; then
        echo "Importing Grafana container app..."
        terraform import "module.container_apps.azurerm_container_app.grafana" "$APP_ID"
      elif [[ "$APP" == "ca-otel-collector-"* ]]; then
        echo "Importing OpenTelemetry collector container app..."
        terraform import "module.container_apps.azurerm_container_app.otel_collector" "$APP_ID"
      elif [[ "$APP" == "ca-tempo-"* ]]; then
        echo "Importing Tempo container app..."
        terraform import "module.container_apps.azurerm_container_app.tempo" "$APP_ID"
      else
        echo "Unknown container app: $APP, skipping import."
      fi
    done
  else
    echo "No container apps found."
  fi
fi

echo "Import completed. Now you can run terraform plan to verify state."

# Get ACR credentials if ACR exists
if az resource show --ids "$ACR_ID" &>/dev/null; then
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
else
  echo "ACR doesn't exist yet, no credentials to retrieve."
fi
