# Integration target, selected for the int branch by infra/targets.json.
# CI supplies shared-vault settings, secrets and the container digest separately.
# Resource names select this workload; the existing resource group supplies location.
resource_group_name  = "rg-xhuma-int"
location             = "uksouth"
app_service_name     = "xhuma-app-int"
redis_name           = "xhuma-redis-int"
postgres_server_name = "xhuma-psql-int"


# Standard C1 cache sizing for the private application cache.
redis_sku_name      = "Standard"
redis_family        = "C"
redis_capacity      = 1
# Integration NHS system and supplier identities used in requests and SDS self lookup.
org_code            = "RRV00"
org_asid            = "200000002574"
device_id           = "1"
xhuma_ods_code      = "RRV00"
xhuma_mhs_party_key = "RRV00-824607"
# int selects the application's built-in integration GP Connect endpoints.
env                 = "int"
app_version         = "0.9.2"

# Wildcards permit all hosts/origins; application mTLS enforcement remains enabled.
allowed_hosts = "*"
cors_origins  = "*"
require_mtls  = "true"

# Empty external URL selects the application's WebSocket relay.
external_relay_url       = ""
external_relay_client_id = "client1"

# Requested domains should match prd in int.
gp_connect_include_allergies      = "false"
gp_connect_include_medication     = "true"
gp_connect_include_problems       = "false"
gp_connect_include_investigations = "false"
gp_connect_include_immunisations  = "false"

# Generated CCDA documents remain cached in Redis for four hours.
ccda_expiry_hours = "4"
