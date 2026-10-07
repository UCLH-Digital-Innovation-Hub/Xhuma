#!/bin/bash
set -euo pipefail

# Define environments
ENVS=("prd-plan" "rg-xhuma-uclh-prd-infra" "rg-xhuma-uclh-prd")

PAYLOAD=$(cat << 'JSON_EOF'
{
  "wait_timer": 0,
  "reviewers": [
    {"type": "User", "id": 63656442},
    {"type": "User", "id": 16974001},
    {"type": "User", "id": 179235262}
  ],
  "deployment_branch_policy": {
    "protected_branches": false,
    "custom_branch_policies": true
  }
}
JSON_EOF
)

for env in "${ENVS[@]}"; do
  echo "Configuring $env..."
  gh api -X PUT "repos/UCLH-Digital-Innovation-Hub/Xhuma/environments/$env" \
    --input - <<< "$PAYLOAD"
    
  echo "Configuring branch policy 'main' for $env..."
  # Check if policy exists
  POLICIES=$(gh api "repos/UCLH-Digital-Innovation-Hub/Xhuma/environments/$env/deployment-branch-policies")
  if ! echo "$POLICIES" | grep -q '"name": "main"'; then
    gh api -X POST "repos/UCLH-Digital-Innovation-Hub/Xhuma/environments/$env/deployment-branch-policies" -f name="main"
  else
    echo "Branch policy 'main' already exists."
  fi
done
