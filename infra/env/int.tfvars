resource_group_name  = "rg-xhuma-int"
location             = "uksouth"
app_service_name     = "xhuma-app-int"
redis_name           = "xhuma-redis-int"
postgres_server_name = "xhuma-psql-int"

# Same inert bootstrap image used by the proven Play target
docker_image = "mcr.microsoft.com/appsvc/staticsite@sha256:23edadf1c0aca901e8532eb145432f49d4e27a20bef92c48710bb4a81142d4ee"

redis_sku_name = "Standard"
redis_family   = "C"
redis_capacity = 1
org_code  = "RRV00"
org_asid  = ""
device_id = "1"
env       = "prod"
app_version = "0.9"

allowed_hosts = "*"
cors_origins  = "*"
require_mtls  = "true"

external_relay_url       = ""
external_relay_client_id = "client1"

gp_connect_include_allergies      = "true"
gp_connect_include_medication     = "true"
gp_connect_include_problems       = "true"
gp_connect_include_investigations = "true"
gp_connect_include_immunisations  = "true"

ccda_expiry_hours = "4"
