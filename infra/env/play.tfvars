resource_group_name  = "rg-xhuma-play"
location             = "uksouth"
app_service_name     = "xhuma-app-play"
redis_name           = "xhuma-redis-play"
postgres_server_name = "xhuma-psql-play"


# Application Settings (Operator must review and populate corresponding secrets)
org_code                 = "RRV00"
org_asid                 = "200000002574"
device_id                = "1"
xhuma_ods_code           = "Z6G1Z"
env                      = "int" # Non-production
app_version              = "0.9.2"
ccda_expiry_hours        = "4"
allowed_hosts            = "*"
cors_origins             = "*"
require_mtls             = "true"
external_relay_url       = ""
external_relay_client_id = "client1"
