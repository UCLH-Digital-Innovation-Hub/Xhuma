# State storage for the shared vault used by INT and play (infra/shared/).
# Keep shared-vault ownership separate from each target's workload state.
resource_group_name  = "rg-xhuma-shared"
storage_account_name = "xtfrgxhumashared"
container_name       = "tfstate"
key                  = "shared.tfstate"
