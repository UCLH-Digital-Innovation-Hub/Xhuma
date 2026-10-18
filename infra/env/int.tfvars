resource_group_name  = "rg-xhuma-int"
location             = "uksouth"
app_service_name     = "xhuma-app-int"
redis_name           = "xhuma-redis-int"
postgres_server_name = "xhuma-psql-int"


redis_sku_name      = "Standard"
redis_family        = "C"
redis_capacity      = 1
org_code            = "RRV00"
org_asid            = "200000002574"
device_id           = "1"
xhuma_ods_code      = "RRV00"
xhuma_mhs_party_key = "RRV00-824607"
env                 = "int"
app_version         = "1.0.0"

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

# dm+d Cache Warmer and Weekly Schedule (Standard target-local infrastructure in INT)
dmd_cache_warm_enabled        = true
dmd_cache_schedule_enabled    = true
dmd_cache_warm_minute         = 0
dmd_cache_schedule_start_time = "2026-10-19T05:00:00"
