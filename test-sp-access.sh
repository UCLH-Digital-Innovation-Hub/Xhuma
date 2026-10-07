#!/bin/bash
set -euo pipefail
export TMP_AZURE_CONFIG="$(mktemp -d)"

export AZURE_CONFIG_DIR="$TMP_AZURE_CONFIG"
az login \
  --service-principal \
  --username "4e390b82-2af5-4387-8617-e9d61bee2108" \
  --password "$XHUMA_PRD_CLIENT_SECRET" \
  --tenant "1d5e3f6e-150d-480f-9d33-1d45ee3a71e1" \
  --output none

az account set --subscription "7b6a1345-50b0-492e-9d53-78833f756987"
az group show --name rg-xhuma-uclh-prd --query name -o tsv

az group show --subscription "c24b0c3e-9e09-4c7c-8687-75e8b654bc8e" --name rg-xhuma-shared --query name -o tsv
az keyvault show --subscription "c24b0c3e-9e09-4c7c-8687-75e8b654bc8e" --resource-group rg-xhuma-shared --name xhuma-shared-kv-int --query name -o tsv
az storage account show --subscription "c24b0c3e-9e09-4c7c-8687-75e8b654bc8e" --resource-group rg-xhuma-shared --name xtfrgxhumashared --query name -o tsv

rm -rf "$TMP_AZURE_CONFIG"
