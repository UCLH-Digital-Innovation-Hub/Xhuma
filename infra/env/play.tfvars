# Rehearsal target, selected for the dev branch by infra/targets.json.
# CI supplies shared-vault settings, secrets and the container digest separately.
# Resource names select this workload; the existing resource group supplies location.
resource_group_name  = "rg-xhuma-play"
location             = "uksouth"
app_service_name     = "xhuma-app-play"
redis_name           = "xhuma-redis-play"
postgres_server_name = "xhuma-psql-play"


# Rehearsal NHS identities. Redis sizing uses the Standard C1 default.
# GP Connect clinical sections are enabled explicitly below.
# Use the commissioned Play NHS identity and MHS party key explicitly.
org_code                 = "RRV00"
org_asid                 = "200000002574"
device_id                = "1"
xhuma_ods_code           = "RRV00"
xhuma_mhs_party_key      = "RRV00-824607"
# The play target runs in int mode, using built-in integration GP Connect endpoints.
env                      = "int" # Non-production
app_version              = "1.0.2"
# Generated CCDA documents remain cached in Redis for four hours.
ccda_expiry_hours        = "4"
# Wildcards permit all hosts/origins; application mTLS enforcement remains enabled.
allowed_hosts            = "*"
cors_origins             = "*"
require_mtls             = "true"
# Empty external URL selects the application's WebSocket relay.
external_relay_url       = ""
external_relay_client_id = "client1"

# Request all supported clinical sections in dev/play.
gp_connect_include_allergies      = "true"
gp_connect_include_medication     = "true"
gp_connect_include_problems       = "true"
gp_connect_include_investigations = "true"
gp_connect_include_immunisations  = "true"

# dm+d Cache Warmer (Manual infrastructure in Play only; scheduler remains disabled)
dmd_cache_warm_enabled     = true
dmd_cache_schedule_enabled = false
