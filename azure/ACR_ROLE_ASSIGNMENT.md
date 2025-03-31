# Azure Container Registry Role Assignment

This document explains how to set up proper access for the Container App to pull images from the Azure Container Registry (ACR) after the initial deployment.

## Background

The Xhuma Container App uses a System-Assigned Managed Identity to authenticate with the Azure Container Registry. This identity needs to be granted the `AcrPull` role on the ACR for the Container App to pull images successfully.

While we've set up a Terraform role assignment to handle this automatically, you may need to perform this step manually if:

1. The initial deployment doesn't complete fully 
2. The Container App was created but the role assignment failed
3. You need to debug authentication issues

## Automatic Role Assignment 

In our Terraform configuration, we've added this role assignment that should be created automatically:

```terraform
resource "azurerm_role_assignment" "xhuma_acr_pull" {
  # The Container App needs to be created first to have a managed identity
  depends_on = [module.container_apps]
  
  # Get the principal ID of the Container App's managed identity
  principal_id         = module.container_apps.xhuma_identity_principal_id
  
  # Use the built-in ACR Pull role
  role_definition_name = "AcrPull"
  
  # Scope to the ACR resource
  scope                = module.acr.id
}
```

## Manual Role Assignment

If you need to set up the role assignment manually, follow these steps:

1. **Get the Container App's Managed Identity Principal ID**:

```bash
PRINCIPAL_ID=$(az containerapp show \
  --name ca-xhuma-play \
  --resource-group rg-xhuma-play \
  --query "identity.principalId" -o tsv)

echo "Container App Managed Identity Principal ID: $PRINCIPAL_ID"
```

2. **Get the ACR Resource ID**:

```bash
ACR_ID=$(az acr show \
  --name crxhumaplay \
  --resource-group rg-xhuma-play \
  --query "id" -o tsv)

echo "ACR Resource ID: $ACR_ID"
```

3. **Create the Role Assignment**:

```bash
az role assignment create \
  --assignee $PRINCIPAL_ID \
  --role AcrPull \
  --scope $ACR_ID
```

4. **Verify the Role Assignment**:

```bash
az role assignment list \
  --assignee $PRINCIPAL_ID \
  --all \
  --include-inherited \
  --output table
```

## Troubleshooting

If the Container App is still having issues pulling images from ACR:

1. Check if the managed identity was created for the Container App
2. Verify that the role assignment exists
3. Ensure the ACR is in the same subscription as the Container App
4. Try restarting the Container App after setting the role assignment

## GitHub Secrets for Container Registry

After the first successful deployment, you can get the ACR credentials and add them as GitHub repository secrets for future deployments:

```bash
# Get ACR details
ACR_NAME=crxhumaplay
ACR_LOGIN_SERVER=$(az acr show --name $ACR_NAME --query loginServer -o tsv)
ACR_CREDS=$(az acr credential show --name $ACR_NAME)

# Extract username and password
ACR_USERNAME=$(echo $ACR_CREDS | jq -r '.username')
ACR_PASSWORD=$(echo $ACR_CREDS | jq -r '.passwords[0].value')

echo "ACR_LOGIN_SERVER: $ACR_LOGIN_SERVER"
echo "ACR_USERNAME: $ACR_USERNAME"
echo "ACR_PASSWORD: $ACR_PASSWORD"
```

Add these as secrets in your GitHub repository:
- `ACR_LOGIN_SERVER`
- `ACR_USERNAME`
- `ACR_PASSWORD`
