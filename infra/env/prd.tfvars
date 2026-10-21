resource_group_name  = "rg-xhuma-uclh-prd"
location             = "uksouth"
app_service_name     = "xhuma-app-prd"
redis_name           = "xhuma-redis-prd"
postgres_server_name = "xhuma-psql-prd"

redis_sku_name = "Standard"
redis_family   = "C"
redis_capacity = 1

org_code            = "RRV00"
org_asid            = "200000087786"
device_id           = "1"
xhuma_ods_code      = "Z6G1Z"
xhuma_mhs_party_key = "Z6G1Z-838808"
env                 = "prod"
app_version         = "1.0.3"
require_mtls        = "true"

allowed_hosts = "*"
cors_origins  = "*"

external_relay_url       = ""
external_relay_client_id = "client1"

gp_connect_include_allergies      = "false"
gp_connect_include_medication     = "true"
gp_connect_include_problems       = "false"
gp_connect_include_investigations = "false"
gp_connect_include_immunisations  = "false"

ccda_expiry_hours      = "4"
nhs_relay_base_path    = "https://proxy.national.ncrs.nhs.uk"
nhs_over_internet_path = ""
dmd_base_url           = "https://ontology.nhs.uk/production2/fhir"

# dm+d Cache Warmer (Standard target-local infrastructure in PRD; schedule deferred)
dmd_cache_warm_enabled     = true
dmd_cache_schedule_enabled = false

ip_restriction_default_action = "Deny"

additional_ip_restrictions = [
  {
    name       = "EpicHosting"
    priority   = 110
    ip_address = "68.65.248.0/21"
  },
  {
    name       = "GPConnectRelay"
    priority   = 120
    ip_address = "195.194.4.65/32"
  }
]
