# State storage for the production shared vault (infra/shared/).
# Use a separate backend from both production workload state and INT/play sharing.
resource_group_name  = "rg-xhuma-shared"
storage_account_name = "xtfrgxhumasharedprd"
container_name       = "tfstate"
key                  = "shared-prd.tfstate"
