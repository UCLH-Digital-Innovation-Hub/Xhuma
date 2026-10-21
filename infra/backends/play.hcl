# State storage for the play target root (infra/), provisioned by bootstrap.
# Pass this file to terraform init with -backend-config.
resource_group_name  = "rg-xhuma-play"
storage_account_name = "xtfrgxhumaplay"
container_name       = "tfstate"
key                  = "terraform.tfstate"
