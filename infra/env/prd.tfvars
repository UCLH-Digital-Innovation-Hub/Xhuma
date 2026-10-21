# Production target, selected for the main branch by infra/targets.json.
# CI supplies shared-vault settings, secrets and the container digest separately.
# Resource names select this workload; the existing resource group supplies location.
resource_group_name  = "rg-xhuma-uclh-prd"
location             = "uksouth"
app_service_name     = "xhuma-app-prd"
redis_name           = "xhuma-redis-prd"
postgres_server_name = "xhuma-psql-prd"

# Standard C1 cache sizing for the private application cache.
redis_sku_name = "Standard"
redis_family   = "C"
redis_capacity = 1

# Production NHS system and supplier identities used in requests and SDS self lookup.
org_code            = "RRV00"
org_asid            = "200000087786"
device_id           = "1"
xhuma_ods_code      = "Z6G1Z"
xhuma_mhs_party_key = "Z6G1Z-838808"
# prod enables production runtime checks and uses the explicit proxy bases below.
env                 = "prod"
app_version         = "1.0.2"
require_mtls        = "true"

# Wildcards permit all hosts/origins; application mTLS enforcement remains enabled.
allowed_hosts = "*"
cors_origins  = "*"

# Empty external URL selects the application's WebSocket relay.
external_relay_url       = ""
external_relay_client_id = "client1"

# Production currently requests medication only from GP Connect.
gp_connect_include_allergies      = "false"
gp_connect_include_medication     = "true"
gp_connect_include_problems       = "false"
gp_connect_include_investigations = "false"
gp_connect_include_immunisations  = "false"

# Generated CCDA documents remain cached in Redis for four hours.
ccda_expiry_hours      = "4"
# Production relay proxy; the empty internet path leaves direct HTTP unconfigured
# and omits the associated NHS client TLS certificate references.
nhs_relay_base_path    = "https://proxy.national.ncrs.nhs.uk"
nhs_over_internet_path = ""
# Production uses the production2 terminology endpoint rather than the default production1.
dmd_base_url           = "https://ontology.nhs.uk/production2/fhir"

# dm+d Cache Warmer (Standard target-local infrastructure in PRD; schedule deferred)
dmd_cache_warm_enabled     = true
dmd_cache_schedule_enabled = false
