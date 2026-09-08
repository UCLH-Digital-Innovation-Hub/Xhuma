#!/usr/bin/env bash
set -euo pipefail

export XHUMA_SUBSCRIPTION_ID="${AZURE_SUBSCRIPTION_ID:-}"
export XHUMA_TENANT_ID="${AZURE_TENANT_ID:-}"
export XHUMA_TARGET="play"
export XHUMA_LOCATION="uksouth"
export XHUMA_RESOURCE_GROUP="rg-xhuma-play"
export XHUMA_STATE_RG="rg-xhuma-state-play"
export XHUMA_STATE_ACCOUNT="xtfrgxhumaplay"
export XHUMA_BOOTSTRAP_OBJECT_ID="${AZURE_BOOTSTRAP_OBJECT_ID:-}"

if [[ -z "$XHUMA_SUBSCRIPTION_ID" || -z "$XHUMA_TENANT_ID" ]]; then
  echo "Missing subscription or tenant ID."
  exit 1
fi

az account set --subscription "$XHUMA_SUBSCRIPTION_ID"

echo "Creating Resource Groups..."
az group create --name "$XHUMA_RESOURCE_GROUP" \
  --location "$XHUMA_LOCATION" --tags application=Xhuma target="$XHUMA_TARGET"
az group create --name "$XHUMA_STATE_RG" \
  --location "$XHUMA_LOCATION" --tags application=Xhuma target="$XHUMA_TARGET"

echo "Creating State Storage Account..."
az storage account create --name "$XHUMA_STATE_ACCOUNT" \
  --resource-group "$XHUMA_STATE_RG" --location "$XHUMA_LOCATION" \
  --sku Standard_LRS --kind StorageV2 --min-tls-version TLS1_2 \
  --allow-blob-public-access false

if [[ -n "$XHUMA_BOOTSTRAP_OBJECT_ID" ]]; then
  XHUMA_STATE_SCOPE="/subscriptions/$XHUMA_SUBSCRIPTION_ID/resourceGroups/$XHUMA_STATE_RG/providers/Microsoft.Storage/storageAccounts/$XHUMA_STATE_ACCOUNT"
  az role assignment create --assignee-object-id "$XHUMA_BOOTSTRAP_OBJECT_ID" \
    --assignee-principal-type User --role "Storage Blob Data Contributor" \
    --scope "$XHUMA_STATE_SCOPE"
  sleep 30 # Allow role to propagate
fi

# We use account-key auth here to ensure it creates if role propagation fails
ACCOUNT_KEY=$(az storage account keys list --resource-group "$XHUMA_STATE_RG" --account-name "$XHUMA_STATE_ACCOUNT" --query '[0].value' -o tsv)

echo "Creating tfstate container..."
az storage container create --name tfstate \
  --account-name "$XHUMA_STATE_ACCOUNT" --account-key "$ACCOUNT_KEY"

echo "Enabling versioning and retention..."
az storage account blob-service-properties update \
  --account-name "$XHUMA_STATE_ACCOUNT" --resource-group "$XHUMA_STATE_RG" \
  --enable-versioning true --enable-delete-retention true \
  --delete-retention-days 30 --enable-container-delete-retention true \
  --container-delete-retention-days 30

echo "Setup for play complete."
