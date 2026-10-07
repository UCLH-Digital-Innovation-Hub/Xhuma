resource_group_name  = "rg-xhuma-uclh-prd"
location             = "uksouth"
app_service_name     = "xhuma-app-prd"
redis_name           = "xhuma-redis-prd"
postgres_server_name = "xhuma-psql-prd"

redis_sku_name = "Standard"
redis_family   = "C"
redis_capacity = 1

org_code       = "RRV00"
# Production ASID not yet issued.
# GP Connect production functional readiness remains blocked until
# the issued production ASID is configured.
org_asid       = ""
device_id      = "1"
env            = "prod"
app_version    = "0.9"
require_mtls   = "true"

allowed_hosts = "*"
cors_origins  = "*"

external_relay_url       = ""
external_relay_client_id = "client1"

gp_connect_include_allergies      = "true"
gp_connect_include_medication     = "true"
gp_connect_include_problems       = "true"
gp_connect_include_investigations = "true"
gp_connect_include_immunisations  = "true"

ccda_expiry_hours = "4"
