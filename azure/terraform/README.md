# Terraform Deployment for Xhuma

This directory contains Terraform configuration for deploying Xhuma to Azure Container Apps.

## Recent Improvements

- Fixed Registry credentials syntax in Container Apps
- Added intelligent resource importing 
- Improved CI/CD pipeline with change detection
- Updated workflows to support multiple environments

## Structure

- `main.tf` - Main configuration file
- `variables.tf` - Variable definitions
- `modules/` - Modularized resources:
  - `acr/` - Azure Container Registry
  - `storage/` - Azure Storage Account
  - `key_vault/` - Azure Key Vault
  - `log_analytics/` - Log Analytics Workspace
  - `container_apps_environment/` - Container Apps Environment
  - `container_apps/` - Container Apps (Xhuma, Redis, PostgreSQL, etc.)

## Prerequisites

- Azure CLI installed
- Terraform ≥ 1.10.5
- Azure subscription with appropriate permissions
- GitHub repository with the required secrets configured

## Running Locally

To deploy manually:

```bash
# Login to Azure
az login --tenant uclhaz.onmicrosoft.com
az account set --subscription <subscription-id>

# Initialize Terraform
terraform init

# Check if resources exist and import them
./import-resources.sh -g rg-xhuma-play

# Plan deployment
terraform plan -var-file=environments/play.tfvars -out=tfplan

# Apply changes
terraform apply tfplan
```

## Managing Existing Resources

If resources already exist in Azure, you can import them into the Terraform state:

```bash
./import-resources.sh -g <resource-group-name>
```

This will automatically detect and import existing resources, making it safe to apply Terraform to existing infrastructure.

## Change Detection

The CI/CD pipeline automatically detects:
- Infrastructure changes (using Terraform plan)
- Application code changes
- Manual triggers

It optimizes the deployment by only rebuilding and pushing container images when necessary.

## Environment Separation

Resources are deployed with environment-specific naming to maintain clean separation:

```
Resource Group: rg-xhuma-{env}
Container Registry: crxhuma{env}
Storage Account: st{env}xhuma
Key Vault: kvxhuma{env}
Container Apps: ca-{component}-{env}
```

Where `{env}` is the environment name (play, dev, test, prod) and `{component}` is the component name (xhuma, redis, postgres, etc.).

## Troubleshooting

If you encounter issues:

1. Check if resources already exist and import them:
   ```bash
   ./import-resources.sh -g <resource-group>
   ```

2. Validate the Terraform configuration:
   ```bash
   terraform validate
   ```

3. Check for syntax errors:
   ```bash
   terraform fmt -check
   ```

4. Try with debugging enabled:
   ```bash
   TF_LOG=DEBUG terraform plan
