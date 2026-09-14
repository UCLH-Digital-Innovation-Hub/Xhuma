resource_group_name        = "rg-xhuma-play"
location                   = "uksouth"
app_service_name           = "xhuma-app-play"
redis_name                 = "xhuma-redis-play"
postgres_server_name       = "xhuma-psql-play"

# Immutable bootstrap image reference for initial creation
docker_image               = "mcr.microsoft.com/appsvc/staticsite@sha256:23edadf1c0aca901e8532eb145432f49d4e27a20bef92c48710bb4a81142d4ee"

# Application Settings (Operator must review and populate corresponding secrets)
org_code                   = "RRV00"
org_asid                   = "200000000000"
device_id                  = "1"
env                        = "int" # Non-production
ccda_expiry_hours          = "4"
allowed_hosts              = "*"
cors_origins               = "*"
require_mtls               = "true"
external_relay_url         = ""
external_relay_client_id   = "client1"
