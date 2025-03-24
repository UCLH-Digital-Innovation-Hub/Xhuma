#!/bin/bash
# Azure deployment script for Xhuma

set -e

# Default values
LOCATION="uksouth"
ACR_SKU="Standard"
STORAGE_SKU="Standard_LRS"
CONTAINER_APP_ENV_NAME="xhuma-env"

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to display help
function show_usage {
    echo -e "Usage: $0 [OPTIONS]"
    echo -e "Deploy Xhuma to Azure"
    echo -e "\nOptions:"
    echo -e "  -g, --resource-group NAME   Resource group name (required)"
    echo -e "  -a, --acr-name NAME         Azure Container Registry name (required)"
    echo -e "  -s, --storage-name NAME     Storage account name (required)"
    echo -e "  -k, --keyvault-name NAME    Key Vault name (required)"
    echo -e "  -l, --location LOCATION     Azure region (default: $LOCATION)"
    echo -e "  -h, --help                  Show this help message and exit"
    echo -e "\nExample:"
    echo -e "  $0 -g xhuma-rg -a xhumaacr -s xhumastorage -k xhumakv"
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    key="$1"
    case $key in
        -g|--resource-group)
            RESOURCE_GROUP="$2"
            shift 2
            ;;
        -a|--acr-name)
            ACR_NAME="$2"
            shift 2
            ;;
        -s|--storage-name)
            STORAGE_NAME="$2"
            shift 2
            ;;
        -k|--keyvault-name)
            KEYVAULT_NAME="$2"
            shift 2
            ;;
        -l|--location)
            LOCATION="$2"
            shift 2
            ;;
        -h|--help)
            show_usage
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            show_usage
            exit 1
            ;;
    esac
done

# Check required parameters
if [ -z "$RESOURCE_GROUP" ] || [ -z "$ACR_NAME" ] || [ -z "$STORAGE_NAME" ] || [ -z "$KEYVAULT_NAME" ]; then
    echo -e "${RED}Error: Missing required parameters${NC}"
    show_usage
    exit 1
fi

# Login to Azure (if not already logged in)
echo -e "${BLUE}Checking Azure login...${NC}"
az account show &> /dev/null || az login

# Set default subscription
echo -e "${BLUE}Setting default subscription...${NC}"
SUBSCRIPTION_ID=$(az account show --query id -o tsv)
echo -e "Using subscription: $SUBSCRIPTION_ID"

# Create resource group
echo -e "${BLUE}Creating resource group: $RESOURCE_GROUP${NC}"
az group create --name "$RESOURCE_GROUP" --location "$LOCATION"

# Create Azure Container Registry
echo -e "${BLUE}Creating Azure Container Registry: $ACR_NAME${NC}"
az acr create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$ACR_NAME" \
  --sku "$ACR_SKU" \
  --admin-enabled true

# Get ACR login server (not sensitive)
ACR_LOGIN_SERVER=$(az acr show --name "$ACR_NAME" --query loginServer -o tsv)

# Create Storage Account
echo -e "${BLUE}Creating Storage Account: $STORAGE_NAME${NC}"
az storage account create \
  --name "$STORAGE_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --sku "$STORAGE_SKU" \
  --kind StorageV2

# Get storage account key securely (don't display)
STORAGE_KEY=$(az storage account keys list --account-name "$STORAGE_NAME" --query "[0].value" -o tsv)

# Create file shares
echo -e "${BLUE}Creating file shares...${NC}"
az storage share create --name redis-data --account-name "$STORAGE_NAME" --account-key "$STORAGE_KEY"
az storage share create --name postgres-data --account-name "$STORAGE_NAME" --account-key "$STORAGE_KEY"

# Create Key Vault
echo -e "${BLUE}Creating Key Vault: $KEYVAULT_NAME${NC}"
az keyvault create \
  --name "$KEYVAULT_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION"

# Create Container Apps Environment
echo -e "${BLUE}Creating Container Apps Environment: $CONTAINER_APP_ENV_NAME${NC}"
az containerapp env create \
  --name "$CONTAINER_APP_ENV_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION"

# Output GitHub Actions secrets information (no actual secrets printed)
echo -e "${GREEN}==== GitHub Actions Secrets ====${NC}"
echo -e "You'll need to add the following secrets to your GitHub repository:"
echo -e "- AZURE_CREDENTIALS (Generate using service principal command below)"
echo -e "- ACR_LOGIN_SERVER: $ACR_LOGIN_SERVER"
echo -e "- ACR_NAME: $ACR_NAME"
echo -e "- ACR_USERNAME (Get from Azure Portal or CLI)"
echo -e "- ACR_PASSWORD (Get from Azure Portal or CLI)"
echo -e "- RESOURCE_GROUP: $RESOURCE_GROUP"
echo -e "- CONTAINER_APP_ENVIRONMENT: $CONTAINER_APP_ENV_NAME"
echo -e "- STORAGE_ACCOUNT_NAME: $STORAGE_NAME"
echo -e "- Plus application secrets like REDIS_PASSWORD, API_KEY, etc."

echo -e "${BLUE}To get ACR credentials securely:${NC}"
echo -e "az acr credential show --name \"$ACR_NAME\" --resource-group \"$RESOURCE_GROUP\""

# Instructions for service principal (no secrets printed)
echo -e "${BLUE}To create a service principal for GitHub Actions:${NC}"
echo -e "az ad sp create-for-rbac --name \"xhuma-github-actions\" --role contributor \\"
echo -e "  --scopes /subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP \\"
echo -e "  --sdk-auth"
echo -e "Use the entire JSON output as the AZURE_CREDENTIALS secret."

echo -e "${GREEN}Azure infrastructure provisioned successfully!${NC}"
