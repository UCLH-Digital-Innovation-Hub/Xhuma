# Xhuma Deployment Guide

This document provides comprehensive information about the deployment process for the Xhuma service on Azure, including CI/CD, infrastructure, and developer instructions.

## Overview

Xhuma is deployed to Azure Container Apps using a simplified CI/CD pipeline with GitHub Actions and Terraform. The deployment:

1. Builds and tests the application using Docker Compose
2. Deploys the infrastructure using Terraform
3. Builds and pushes Docker images to Azure Container Registry (ACR)
4. Deploys the application to Azure Container Apps
5. Performs a basic health check

## Prerequisites

- Azure subscription with access to Azure Container Apps, Azure Cache for Redis, etc.
- GitHub repository with appropriate secrets configured
- Docker and Docker Compose installed locally for development

## Required Secrets

The following secrets must be configured in GitHub repository environments:

| Secret | Description | How to Obtain |
|--------|-------------|---------------|
| `AZURE_CREDENTIALS` | Service principal credentials JSON | Create via Azure CLI (see below) |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription ID | From Azure Portal or `az account show` |
| `RESOURCE_GROUP` | Resource group name (e.g., `rg-xhuma-play`) | Already created in Azure |
| `REDIS_PASSWORD` | Password for Redis | Generate securely |
| `POSTGRES_PASSWORD` | Password for PostgreSQL | Generate securely |
| `API_KEY` | API key for NHS Digital services | From NHS Digital Developer Portal |
| `GRAFANA_ADMIN_PASSWORD` | Admin password for Grafana | Generate securely |
| `ACR_USERNAME` | Azure Container Registry username | Available after ACR creation |
| `ACR_PASSWORD` | Azure Container Registry password | Available after ACR creation |
| `ACR_LOGIN_SERVER` | ACR login server URL | Available after ACR creation |

### Creating a Service Principal (for AZURE_CREDENTIALS)

```bash
# Login to Azure
az login --tenant uclhaz.onmicrosoft.com

# Set the correct subscription
az account set --subscription "$SUBSCRIPTION_ID"

# Create a service principal with Contributor role
az ad sp create-for-rbac --name "xhuma-github-actions" \
  --role contributor \
  --scopes /subscriptions/$SUBSCRIPTION_ID/resourceGroups/rg-xhuma-play \
  --sdk-auth
```

The output JSON should be stored as the `AZURE_CREDENTIALS` secret.

## CI/CD Pipeline

### CI Workflow (.github/workflows/ci.yml)

The CI workflow runs on pull requests and pushes to the `simplified-deployment` branch:

1. Builds the Docker image 
2. Runs tests using docker-compose.test.yml
3. Reports success or failure

### CD Workflow (.github/workflows/cd.yml)

The CD workflow runs on pushes to the `simplified-deployment` branch:

1. Builds and pushes the Docker image to ACR
2. Initializes Terraform
3. Plans and applies Terraform changes
4. Performs a health check against the deployed application

## Infrastructure as Code

The infrastructure is defined using Terraform in the `infra/` directory:

```
infra/
├── main.tf           # Main Terraform configuration
├── variables.tf      # Variable definitions
├── outputs.tf        # Output definitions
├── terraform.tfvars.example # Example variable values
└── modules/
    ├── container_apps/                # Container Apps configuration
    ├── container_apps_environment/    # Container Apps Environment
    ├── log_analytics/                 # Log Analytics Workspace
    └── redis/                         # Azure Cache for Redis
```

### Manual Terraform Import

In some cases, you may need to manually import existing resources:

```bash
# Set your variables
SUBSCRIPTION_ID=$(az account show --query id -o tsv)
RESOURCE_GROUP="rg-xhuma-play"

# Navigate to the Terraform directory
cd infra

# Import existing ACR (if already created)
terraform import module.acr.azurerm_container_registry.acr \
  "/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RESOURCE_GROUP}/providers/Microsoft.ContainerRegistry/registries/crxhumaplay"
```

## Development Setup

### Local Development

1. Clone the repository
2. Copy `.env.example` to `.env` and configure environment variables
3. Run the application locally:

```bash
docker-compose up
```

### Running Tests

Tests can be run using the test Docker Compose configuration:

```bash
docker-compose -f docker-compose.test.yml up
```

### Manual Deployment

While CI/CD is the preferred method, you can deploy manually:

```bash
# Login to Azure
az login

# Build and push Docker image
docker build -t $ACR_LOGIN_SERVER/xhuma:latest .
docker push $ACR_LOGIN_SERVER/xhuma:latest

# Deploy with Terraform
cd infra
terraform init
terraform apply
```

## Observability

### Current Status

The deployment includes basic observability through:

- Container Apps logs integrated with Log Analytics
- A health endpoint at `/health`
- CPU and memory metrics via Container Apps

### Future Roadmap

A more comprehensive observability solution is planned:

1. **Prometheus and Grafana**: For metrics collection and visualization
2. **OpenTelemetry**: For distributed tracing
3. **Tempo**: For trace storage and visualization

This will be configured in future iterations using the existing configuration files:
- `prometheus.yml`
- `otel-collector-config.yaml`
- `tempo.yaml`

## Troubleshooting

### Common Issues

1. **ACR Authentication Failures**:
   - Verify ACR_USERNAME, ACR_PASSWORD, and ACR_LOGIN_SERVER secrets are correct
   - Check service principal has appropriate ACR permissions

2. **Container App Deployment Failures**:
   - Check Container App logs in Azure Portal
   - Verify environment variables are correctly configured
   - Review Terraform apply logs for errors

3. **Networking Issues**:
   - Ensure Container Apps Environment is properly configured
   - Check DNS settings and ingress configuration

### Accessing Logs

```bash
# Get Container App logs
az containerapp logs show -n ca-xhuma-play -g rg-xhuma-play --follow
```

## Security Notes

- Managed Identity integration will be added in future iterations
- Key Vault integration is prepared for future use but not currently implemented
- All secrets are currently stored in GitHub Secrets
