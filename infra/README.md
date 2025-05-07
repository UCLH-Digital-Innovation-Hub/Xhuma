# Xhuma Infrastructure as Code

This directory contains the Terraform configuration for deploying Xhuma to Azure Container Apps.

## Overview

The infrastructure includes:

- Container Apps for Xhuma
- Container App Environment
- Azure Cache for Redis
- Log Analytics Workspace

## Key Security Features

- **JWT Private Key Security**: Private keys are never stored in Docker images or on disk
- **Environment Variable Injection**: All secrets are passed via environment variables at runtime
- **Secure Defaults**: Terraform validation ensures required secrets are provided

## Quick Start

### Prerequisites

1. Azure subscription and login
2. Terraform CLI installed
3. Existing Azure resource group (`rg-xhuma-play`)
4. Existing Azure Container Registry (ACR)
5. Required secrets (API key, JWT key, Redis password)

### Setup

1. Copy `terraform.tfvars.example` to `terraform.tfvars` (excluded from git by `.gitignore`)
2. Fill in the required secrets
3. Initialize Terraform:

```bash
terraform init
```

4. Import existing resources:

```bash
./import-resources.sh <your-subscription-id>
```

5. Run Terraform:

```bash
terraform validate
terraform plan
terraform apply
```

### Using Environment Variables

For CI/CD pipelines, you can pass secrets via environment variables:

```bash
export TF_VAR_jwtkey="$(cat /path/to/private_key.pem)"
export TF_VAR_api_key="your-api-key"
export TF_VAR_redis_password="your-redis-password"
```

## Terraform: Importing Pre-Existing Resources

Some Azure resources already exist and need to be imported into the Terraform state rather than being recreated. The project includes an import script to handle this:

### Using the Import Script

```bash
# Run the script with your Azure subscription ID
./import-resources.sh <subscription-id>
```

This script will:
1. Check if resources are already in the Terraform state
2. Import any missing resources (Log Analytics Workspace, Redis Cache)
3. Skip already imported resources to avoid errors

### Manually Importing Resources

If you need to manually import resources:

```bash
# Log Analytics Workspace
terraform import module.log_analytics.azurerm_log_analytics_workspace.workspace \
  "/subscriptions/<subscription-id>/resourceGroups/rg-xhuma-play/providers/Microsoft.OperationalInsights/workspaces/logxhumaplay"

# Redis Cache
terraform import module.redis.azurerm_redis_cache.redis \
  "/subscriptions/<subscription-id>/resourceGroups/rg-xhuma-play/providers/Microsoft.Cache/redis/redis-xhuma-play"
```

### CI/CD Integration

The GitHub Actions workflow (`cd.yml`) automatically runs the import script before running Terraform to ensure resources are correctly managed.

## JWTKEY Variable

The `jwtkey` variable is required and must contain a PEM-formatted RSA private key for JWT signing. This key is:

- Never stored in Docker images or on disk
- Passed securely at runtime via environment variable
- Validated before deployment to prevent errors
- Used to dynamically generate JWK for token verification

Each Xhuma container gets this key injected as the `JWTKEY` environment variable.

## Security Best Practices

1. **Never commit `terraform.tfvars` to version control**
2. **Use Terraform validations** to catch missing required variables
3. **Mark sensitive variables** with `sensitive = true`
4. **Set appropriate lifecycle rules** to prevent accidental deletion
5. **Use CI/CD with secrets management** (GitHub Secrets, Azure Key Vault)

## Key Rotation

To rotate the JWT signing key:

1. Generate a new RSA key pair
2. Update the `jwtkey` variable with the new private key
3. Roll out the change with Terraform
4. The `/jwk` and `/jwks` endpoints will automatically expose the new public key

## Troubleshooting

### Provider Registration Errors

The configuration uses `skip_provider_registration = true` to avoid errors with missing Azure Resource Providers during deployment. This is particularly useful in CI/CD environments where the service principal may not have permissions to register providers.

If you encounter the error:
```
Error: Error ensuring Resource Providers are registered.
```

This is already handled in the configuration by:
1. Setting `skip_provider_registration = true` in the provider block
2. Setting `ARM_SKIP_PROVIDER_REGISTRATION=true` in the CI/CD workflows

### Resource Already Exists Errors

If you see errors like:
```
Error: A resource with the ID "..." already exists - to be managed via Terraform this resource needs to be imported into the State.
```

Run the import script to add existing resources to the Terraform state:
```
./import-resources.sh <subscription-id>
```

### Key Validation Errors

If you encounter the error `"Missing required argument: The argument "jwtkey" is required"`:

- Ensure `jwtkey` is provided in `terraform.tfvars` or as an environment variable
- Check that the private key is in valid PEM format
- Verify that the key is an RSA private key (required for RS512 algorithm)
