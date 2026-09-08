#!/usr/bin/env bash
set -euo pipefail

export XHUMA_SUBSCRIPTION_ID="${AZURE_SUBSCRIPTION_ID:-}"
export XHUMA_TENANT_ID="${AZURE_TENANT_ID:-}"
export XHUMA_TARGET="play"
export XHUMA_LOCATION="uksouth"
export XHUMA_RESOURCE_GROUP="rg-xhuma-play"
export XHUMA_STATE_ACCOUNT="xtfrgxhumaplay"

if [[ -z "$XHUMA_SUBSCRIPTION_ID" || -z "$XHUMA_TENANT_ID" ]]; then
  echo "Missing subscription or tenant ID."
  exit 1
fi

az account set --subscription "$XHUMA_SUBSCRIPTION_ID"

# Verify authenticated tenant and subscription match expected
AUTH_TENANT=$(az account show --query tenantId -o tsv)
if [[ "$AUTH_TENANT" != "$XHUMA_TENANT_ID" ]]; then
  echo "Authenticated tenant $AUTH_TENANT does not match expected $XHUMA_TENANT_ID"
  exit 1
fi

echo "Checking if Resource Group $XHUMA_RESOURCE_GROUP exists..."
if ! az group show --name "$XHUMA_RESOURCE_GROUP" &>/dev/null; then
  echo "Resource Group $XHUMA_RESOURCE_GROUP does not exist. Please create it manually first."
  exit 1
fi

echo "Ensuring Storage Account $XHUMA_STATE_ACCOUNT exists in $XHUMA_RESOURCE_GROUP..."
SA_STATUS=$(az storage account show --name "$XHUMA_STATE_ACCOUNT" --resource-group "$XHUMA_RESOURCE_GROUP" --query "name" -o tsv 2>&1 || true)
if [[ "$SA_STATUS" == *"ResourceNotFound"* ]] || [[ -z "$SA_STATUS" ]]; then
  az storage account create --resource-group "$XHUMA_RESOURCE_GROUP" \
    --name "$XHUMA_STATE_ACCOUNT" --sku Standard_LRS \
    --encryption-services blob --min-tls-version TLS1_2 \
    --allow-blob-public-access false
elif [[ "$SA_STATUS" == *"AuthorizationFailed"* ]]; then
  echo "Authorization failed when checking storage account."
  exit 1
fi

ACCOUNT_KEY=$(az storage account keys list --resource-group "$XHUMA_RESOURCE_GROUP" --account-name "$XHUMA_STATE_ACCOUNT" --query '[0].value' -o tsv)
echo "::add-mask::$ACCOUNT_KEY"

echo "Creating tfstate container..."
C_STATUS=$(az storage container show --name tfstate --account-name "$XHUMA_STATE_ACCOUNT" --account-key "$ACCOUNT_KEY" --query "name" -o tsv 2>&1 || true)
if [[ "$C_STATUS" == *"NotFound"* ]] || [[ -z "$C_STATUS" ]]; then
  az storage container create --name tfstate --account-name "$XHUMA_STATE_ACCOUNT" --account-key "$ACCOUNT_KEY"
fi

echo "Creating tfplans container..."
P_STATUS=$(az storage container show --name tfplans --account-name "$XHUMA_STATE_ACCOUNT" --account-key "$ACCOUNT_KEY" --query "name" -o tsv 2>&1 || true)
if [[ "$P_STATUS" == *"NotFound"* ]] || [[ -z "$P_STATUS" ]]; then
  az storage container create --name tfplans --account-name "$XHUMA_STATE_ACCOUNT" --account-key "$ACCOUNT_KEY"
fi

echo "Enabling versioning and retention..."
az storage account blob-service-properties update \
  --account-name "$XHUMA_STATE_ACCOUNT" --resource-group "$XHUMA_RESOURCE_GROUP" \
  --enable-versioning true --enable-delete-retention true \
  --delete-retention-days 30 --enable-container-delete-retention true \
  --container-delete-retention-days 30

echo "Setup for play complete."
