resource_group_name  = "rg-xhuma-play"
location             = "uksouth"
app_service_name     = "xhuma-app-play"
redis_name           = "xhuma-redis-play"
postgres_server_name = "xhuma-psql-play"


# Application Settings (Operator must review and populate corresponding secrets)
org_code                 = "RRV00"
org_asid                 = "200000002574"
device_id                = "1"
xhuma_ods_code           = "RRV00"
xhuma_mhs_party_key      = "RRV00-824607"
env                      = "int" # Non-production
app_version              = "1.0.2"
ccda_expiry_hours        = "4"
allowed_hosts            = "*"
cors_origins             = "*"
require_mtls             = "true"
external_relay_url       = ""
external_relay_client_id = "client1"

# dm+d Cache Warmer (Manual infrastructure in Play only; scheduler remains disabled)
dmd_cache_warm_enabled     = true
dmd_cache_schedule_enabled = false
