import asyncio
import hashlib
import hmac
import json
import logging
import os
import pprint
import uuid

import fastapi
import httpx

from app.logging import log_request, log_response
from app.redis_connect import redis_client
from app.security import pds_jwt

BASE_PATH = "https://sandbox.api.service.nhs.uk/"
DEV_BASE_PATH = "https://dev.api.service.nhs.uk/"
INT_BASE_PATH = "https://int.api.service.nhs.uk/"
API_KEY = os.getenv("API_KEY", "TEST_KEY")
PDS_CACHE_HOURS = int(os.getenv("PDS_CACHE_HOURS", 24))
SDS_CACHE_HOURS = int(os.getenv("SDS_CACHE_HOURS", 12))

# router = fastapi.APIRouter(prefix="/pds")


def pds_cache_key(nhsno: int, secret: str = None) -> str:
    """Return a deterministic, pseudonymous Redis key for a patient lookup."""
    secret = secret or os.getenv("PDS_CACHE_HMAC_SECRET") or API_KEY
    digest = hmac.new(secret.encode("utf-8"), str(nhsno).encode("utf-8"), hashlib.sha256).hexdigest()
    return f"pds:patient:{digest}"


def sds_cache_key(ods: str, endpoint: bool = False, partykey: str = None) -> str:
    """Return the deterministic Redis key for an SDS query."""
    resource = "endpoint" if endpoint else "device"
    key = f"pds:sds:{resource}:{ods.upper()}"
    return f"{key}:{partykey}" if endpoint else key


# @router.get("/lookup_patient/{nhsno}")
async def lookup_patient(nhsno: int, request: fastapi.Request = None):
    cache_key = pds_cache_key(nhsno)
    cached_patient = redis_client.get(cache_key)
    if cached_patient:
        logging.info("Cache hit for PDS patient query")
        if isinstance(cached_patient, bytes):
            cached_patient = cached_patient.decode("utf-8")
        return json.loads(cached_patient)

    logging.info("Cache miss for PDS patient query. Fetching from PDS API.")

    def get_pds_token(kid: str):
        full_path = f"{INT_BASE_PATH}oauth2/token"
        jwt_token = pds_jwt(API_KEY, API_KEY, full_path, kid)
        # print(f"jwt_token: {jwt_token}")

        oauth_params = {
            "grant_type": "client_credentials",
            "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
            "client_assertion": jwt_token,
        }
        r = httpx.post(full_path, data=oauth_params)

        response_dict = json.loads(r.text)
        if "access_token" not in response_dict:
            error_msg = f"Failed to retrieve PDS access token. NHS API Response: {r.text}"
            logging.error(error_msg)
            print(f"CRITICAL NHS AUTH ERROR: {error_msg}", flush=True)  # Print directly to Azure logs
            raise fastapi.HTTPException(status_code=500, detail="NHS API Authentication Failed")

        nhs_token = response_dict["access_token"]

        redis_client.setex("access_token", response_dict["expires_in"], nhs_token)
        return nhs_token

    # if nhs token expired or not request, get one and cache

    if not redis_client.exists("access_token"):
        logging.info("NHS token expired or not found, getting new one")
        # Extract dynamically generated Key ID, fallback to 'test-1'
        kid = "test-1"
        if request and hasattr(request.app.state, "jwk_json") and request.app.state.jwk_json:
            kid = request.app.state.jwk_json.get("kid", "test-1")

        nhs_token = get_pds_token(kid)
    else:
        logging.info("NHS token found in cache")
        nhs_token = redis_client.get("access_token").decode("utf-8")

    # print(f"nhs_token: {nhs_token}")
    # set headers for pds request
    headers = {
        "X-Request-ID": str(uuid.uuid4()),
        "X-Correlation-ID": str(uuid.uuid4()),
        # TODO make end user organisation dynamic
        "NHSD-End-User-Organisation-ODS": "Y12345",
        "Authorization": f"Bearer {nhs_token}",
        "accept": "application/fhir+json",
    }

    url = f"{INT_BASE_PATH}personal-demographics/FHIR/R4/Patient/{nhsno}"
    # print(url)
    async with httpx.AsyncClient(event_hooks={"request": [log_request], "response": [log_response]}) as client:
        r = await client.get(url, headers=headers)

    patient_dict = json.loads(r.text)

    redis_client.setex(cache_key, PDS_CACHE_HOURS * 60 * 60, json.dumps(patient_dict))
    return patient_dict


# @router.get("/sds/{ods}")
async def sds_trace(ods: str, endpoint: bool = False, **kwargs):
    """
    Function to get the SDS trace for an ODS code

    args:
    ods: str - the ODS code to trace
    endpoint: bool - whether to make an endpoint SDS trace

    returns:
    fhir bundle of the SDS trace
    """
    partykey = kwargs.get("mhsparty")
    cache_key = sds_cache_key(ods, endpoint, partykey)
    cached_trace = redis_client.get(cache_key)
    if cached_trace:
        logging.info("Cache hit for SDS query %s", cache_key)
        if isinstance(cached_trace, bytes):
            cached_trace = cached_trace.decode("utf-8")
        return json.loads(cached_trace)

    logging.info("Cache miss for SDS query %s. Fetching from SDS API.", cache_key)

    if endpoint:
        suffix = "Endpoint"
        identifier = [
            "https://fhir.nhs.uk/Id/nhsServiceInteractionId|urn:nhs:names:services:gpconnect:fhir:operation:gpc.getstructuredrecord-1",
            f"https://fhir.nhs.uk/Id/nhsMhsPartyKey|{partykey}",
        ]

    else:
        suffix = "Device"
        identifier = [
            # "https://fhir.nhs.uk/Id/nhsServiceInteractionId|urn:nhs:names:services:psis:REPC_IN150016UK05"
            "https://fhir.nhs.uk/Id/nhsServiceInteractionId|urn:nhs:names:services:gpconnect:fhir:operation:gpc.getstructuredrecord-1"
        ]

    url = f"{INT_BASE_PATH}spine-directory/FHIR/R4/{suffix}"
    organisation = f"https://fhir.nhs.uk/Id/ods-organization-code|{ods}"
    # organisation = f"https://fhir.nhs.uk/Id/ods-organization-code|YES"

    api_key = os.environ.get("API_KEY")
    parameters = {
        "organization": organisation,
        "identifier": identifier,
    }
    # print(parameters)
    headers = {
        "X-Request-ID": str(uuid.uuid4()),
        "accept": "application/fhir+json",
        "apikey": api_key,
    }
    r = httpx.get(url, headers=headers, params=parameters)
    if r.status_code != 200:
        raise Exception(f"{r.status_code}: {r.text}")

    trace = json.loads(r.text)
    redis_client.setex(cache_key, SDS_CACHE_HOURS * 60 * 60, json.dumps(trace))
    return trace


if __name__ == "__main__":
    patient = asyncio.run(lookup_patient(9658218873))
    pprint.pprint(patient)

    # print(patient.gender)
    # print(patient.name[0].family)
    # print(patient.generalPractitioner[0].identifier.value)

    # ods = asyncio.run(sds_trace("A82038"))
    # pprint.pprint(ods)
    # for i in ods["entry"]:
    #     pprint.pprint(i)

    # try self lookup
    prefix = "https://fhir.nhs.uk/Id/"
    "https://fhir.nhs.uk/Id/objectClass|nhsAs"
    parameters = {
        "nhsIDCode": "RVV00",
        "objectClass": "nhsAs",
        "nhsAsSvcIAD": "urn:nhs:names:services:gpconnect:fhir:operation:gpc.getstructuredrecord-1",
        "nhsMhsManufacturerOrg": "RRV00",
    }
    indentifiers = [
        f"{prefix}nhsIDCode|RVV00",
        f"{prefix}objectClass|nhsAs",
    ]
    # url = f"{INT_BASE_PATH}spine-directory/FHIR/R4/{suffix}"
    # r = httpx.get(url, headers=headers, params=parameters)
