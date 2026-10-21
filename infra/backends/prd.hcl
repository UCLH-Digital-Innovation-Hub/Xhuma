# State storage for the production target root (infra/), provisioned by bootstrap.
# Pass this file to terraform init with -backend-config.
resource_group_name  = "rg-xhuma-uclh-prd"
storage_account_name = "xtfrgxhumauclhprd"
container_name       = "tfstate"
key                  = "terraform.tfstate"
