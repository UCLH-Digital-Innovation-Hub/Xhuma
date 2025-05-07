#!/bin/bash
# Terraform Resource Import Script for Xhuma
# ------------------------------------------
# This script handles importing existing Azure resources into Terraform state
# to prevent "resource already exists" errors when applying Terraform configs.
#
# Usage: 
#   ./import-resources.sh <subscription_id>
#
# This is used both by the CI/CD pipeline and for local development.

set -e  # Exit on any error

# Check if subscription ID is provided
if [ -z "$1" ]; then
  echo "Error: Subscription ID is required"
  echo "Usage: ./import-resources.sh <subscription_id>"
  exit 1
fi

SUBSCRIPTION_ID="$1"
RESOURCE_GROUP="rg-xhuma-play"
LOG_ANALYTICS_NAME="logxhumaplay"
REDIS_NAME="redis-xhuma-play"

echo "🔄 Importing existing Azure resources into Terraform state..."
echo "Subscription: $SUBSCRIPTION_ID"
echo "Resource Group: $RESOURCE_GROUP"

# Function to check if a resource is already in state
resource_in_state() {
  local resource_address="$1"
  terraform state list | grep -q "$resource_address"
  return $?
}

# Change to the directory containing Terraform configuration
cd "$(dirname "$0")"

# Initialize Terraform (if not already initialized)
terraform init -reconfigure

# Import Log Analytics Workspace if not already in state
if ! resource_in_state "module.log_analytics.azurerm_log_analytics_workspace.workspace"; then
  echo "📝 Importing Log Analytics Workspace: $LOG_ANALYTICS_NAME"
  terraform import module.log_analytics.azurerm_log_analytics_workspace.workspace \
    "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.OperationalInsights/workspaces/$LOG_ANALYTICS_NAME"
else
  echo "✅ Log Analytics Workspace already in Terraform state"
fi

# Import Redis Cache if not already in state
if ! resource_in_state "module.redis.azurerm_redis_cache.redis"; then
  echo "📝 Importing Redis Cache: $REDIS_NAME"
  terraform import module.redis.azurerm_redis_cache.redis \
    "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.Cache/redis/$REDIS_NAME"
else
  echo "✅ Redis Cache already in Terraform state"
fi

# Additional resources can be added here as needed
# For example, Key Vault, Storage Account, etc.

echo "✅ Import process completed"
