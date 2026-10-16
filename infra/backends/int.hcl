# State storage for the INT target root (infra/), provisioned by bootstrap.
# Pass this file to terraform init with -backend-config.
resource_group_name  = "rg-xhuma-int"
storage_account_name = "xtfrgxhumaint"
container_name       = "tfstate"
key                  = "terraform.tfstate"
