resource_group_name  = "rg-xhuma-uclh-prd"
location             = "uksouth"
app_service_name     = "xhuma-app-prd"
redis_name           = "xhuma-redis-prd"
postgres_server_name = "xhuma-psql-prd"

redis_sku_name = "Standard"
redis_family   = "C"
redis_capacity = 1

org_code       = "RRV00"
org_asid       = "200000087786"
device_id      = "1"
env            = "prod"
app_version    = "0.9.1"
require_mtls   = "true"

allowed_hosts = "*"
cors_origins  = "*"

external_relay_url       = ""
external_relay_client_id = "client1"

gp_connect_include_allergies      = "false"
gp_connect_include_medication     = "true"
gp_connect_include_problems       = "false"
gp_connect_include_investigations = "false"
gp_connect_include_immunisations  = "false"

ccda_expiry_hours = "4"
nhs_relay_base_path = "https://proxy.national.ncrs.nhs.uk"
nhs_over_internet_path = ""
dmd_base_url = "https://ontology.nhs.uk/production2/fhir"
saml_trusted_issuer = "CN=mychart.ET1053.epichosted.com,O=Epic Systems Corporation,L=Verona,ST=Wisconsin,C=US"
