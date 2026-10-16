# Rehearsal target, selected for the dev branch by infra/targets.json.
# CI supplies shared-vault settings, secrets and the container digest separately.
# Resource names select this workload; the existing resource group supplies location.
resource_group_name  = "rg-xhuma-play"
location             = "uksouth"
app_service_name     = "xhuma-app-play"
redis_name           = "xhuma-redis-play"
postgres_server_name = "xhuma-psql-play"


# Rehearsal NHS identities. Redis sizing and GP Connect section flags use the
# defaults in variables.tf (Standard C1 and all sections enabled).
# Omitting xhuma_mhs_party_key lets the application discover it through SDS.
org_code                 = "RRV00"
org_asid                 = "200000002574"
device_id                = "1"
xhuma_ods_code           = "Z6G1Z"
# The play target runs in int mode, using built-in integration GP Connect endpoints.
env                      = "int" # Non-production
app_version              = "0.9.2"
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
