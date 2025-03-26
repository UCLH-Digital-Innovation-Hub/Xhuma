#!/bin/bash
# Xhuma Azure Terraform Deployment Script

set -e

# Default values
ENVIRONMENT="play"
AUTO_APPROVE=false
DESTROY=false

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to display help
function show_usage {
    echo -e "Usage: $0 [OPTIONS]"
    echo -e "Deploy Xhuma to Azure using Terraform"
    echo -e "\nOptions:"
    echo -e "  -e, --environment ENV       Environment name: play, dev, test, prod (default: $ENVIRONMENT)"
    echo -e "  -a, --auto-approve          Auto-approve Terraform apply/destroy"
    echo -e "  -d, --destroy               Destroy the infrastructure instead of creating it"
    echo -e "  -h, --help                  Show this help message and exit"
    echo -e "\nExample:"
    echo -e "  $0 -e play                  # Deploy to play environment"
    echo -e "  $0 -e dev -a                # Deploy to dev environment with auto-approve"
    echo -e "  $0 -e play -d               # Destroy play environment"
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    key="$1"
    case $key in
        -e|--environment)
            ENVIRONMENT="$2"
            shift 2
            ;;
        -a|--auto-approve)
            AUTO_APPROVE=true
            shift
            ;;
        -d|--destroy)
            DESTROY=true
            shift
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

# Check for required environment variables
if [ -z "$TF_VAR_redis_password" ]; then
    echo -e "${RED}Error: TF_VAR_redis_password environment variable is required${NC}"
    echo -e "Set it with: export TF_VAR_redis_password='your-password'"
    exit 1
fi

if [ -z "$TF_VAR_postgres_password" ]; then
    echo -e "${RED}Error: TF_VAR_postgres_password environment variable is required${NC}"
    echo -e "Set it with: export TF_VAR_postgres_password='your-password'"
    exit 1
fi

if [ -z "$TF_VAR_api_key" ]; then
    echo -e "${RED}Error: TF_VAR_api_key environment variable is required${NC}"
    echo -e "Set it with: export TF_VAR_api_key='your-api-key'"
    exit 1
fi

if [ -z "$TF_VAR_grafana_admin_password" ]; then
    echo -e "${RED}Error: TF_VAR_grafana_admin_password environment variable is required${NC}"
    echo -e "Set it with: export TF_VAR_grafana_admin_password='your-password'"
    exit 1
fi

# Login to Azure (if not already logged in)
echo -e "${BLUE}Checking Azure login...${NC}"
if ! az account show &> /dev/null; then
    echo -e "${BLUE}Logging in to Azure...${NC}"
    az login --tenant uclhaz.onmicrosoft.com
fi

# Set default subscription
echo -e "${BLUE}Setting default subscription...${NC}"
az account set --subscription "rg-xhuma-play"
echo -e "Using subscription: $(az account show --query name -o tsv)"

# Navigate to the Terraform directory
cd "$(dirname "$0")"

# Initialize Terraform if needed
if [ ! -d ".terraform" ]; then
    echo -e "${BLUE}Initializing Terraform...${NC}"
    terraform init
fi

# Set environment variable
export TF_VAR_environment="$ENVIRONMENT"

if [ "$DESTROY" = true ]; then
    echo -e "${RED}WARNING: This will destroy all resources in the $ENVIRONMENT environment!${NC}"
    echo -e "You have 5 seconds to cancel (Ctrl+C)..."
    sleep 5
    
    if [ "$AUTO_APPROVE" = true ]; then
        echo -e "${BLUE}Destroying infrastructure...${NC}"
        terraform destroy -var="environment=$ENVIRONMENT" -auto-approve
    else
        echo -e "${BLUE}Planning destruction...${NC}"
        terraform plan -destroy -var="environment=$ENVIRONMENT" -out=tfplan
        
        echo -e "${BLUE}Applying destruction plan...${NC}"
        terraform apply tfplan
    fi
else
    echo -e "${BLUE}Planning deployment to $ENVIRONMENT environment...${NC}"
    terraform plan -var="environment=$ENVIRONMENT" -out=tfplan
    
    if [ "$AUTO_APPROVE" = true ]; then
        echo -e "${BLUE}Applying Terraform plan...${NC}"
        terraform apply tfplan
    else
        echo -e "${BLUE}To apply this plan, run:${NC}"
        echo -e "terraform apply tfplan"
    fi
fi

# Display outputs if not destroying
if [ "$DESTROY" = false ] && [ -f "tfplan" ]; then
    echo -e "${GREEN}Deployment to $ENVIRONMENT environment completed!${NC}"
    echo -e "${BLUE}Outputs:${NC}"
    terraform output
fi
