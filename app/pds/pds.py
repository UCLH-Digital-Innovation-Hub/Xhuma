import asyncio
import json
import logging
import os
import pprint
import uuid

import fastapi
import httpx

from app.gp_connect_config import PDS_PATH
from app.logging import log_request, log_response
from app.redis_connect import redis_client
from app.security import pds_jwt

BASE_PATH = "https://sandbox.api.service.nhs.uk/"
DEV_BASE_PATH = "https://dev.api.service.nhs.uk/"
INT_BASE_PATH = "https://int.api.service.nhs.uk/"
API_KEY = os.getenv("API_KEY", "TEST_KEY")

router = fastapi.APIRouter(prefix="/pds")


@router.get("/lookup_patient/{nhsno}")
async def lookup_patient(nhsno: int):
    def get_pds_token():
        full_path = f"{PDS_PATH}oauth2/token"
        jwt_token = pds_jwt(API_KEY, API_KEY, full_path, "test-1")
        # print(f"jwt_token: {jwt_token}")

        oauth_params = {
            "grant_type": "client_credentials",
            "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
            "client_assertion": jwt_token,
        }
        r = httpx.post(full_path, data=oauth_params)

        response_dict = json.loads(r.text)
        # print(response_dict)
        nhs_token = response_dict["access_token"]

        redis_client.setex("access_token", response_dict["expires_in"], nhs_token)
        return nhs_token

    # if nhs token expired or not request, get one and cache

    if not redis_client.exists("access_token"):
        logging.info("NHS token expired or not found, getting new one")
        nhs_token = get_pds_token()
    else:
        logging.info("NHS token found in cache")
        nhs_token = redis_client.get("access_token").decode("utf-8")

    # print(f"nhs_token: {nhs_token}")
    # set headers for pds request
    headers = {
        "X-Request-ID": str(uuid.uuid4()),
        "X-Correlation-ID": str(uuid.uuid4()),
        "NHSD-End-User-Organisation-ODS": os.getenv("ORG_CODE", "RRV00"),
        "Authorization": f"Bearer {nhs_token}",
        "accept": "application/fhir+json",
    }

    url = f"{PDS_PATH}personal-demographics/FHIR/R4/Patient/{nhsno}"
    # print(url)
    async with httpx.AsyncClient(
        event_hooks={"request": [log_request], "response": [log_response]}
    ) as client:
        r = await client.get(url, headers=headers)

    patient_dict = json.loads(r.text)

    return patient_dict


@router.get("/sds/{ods}")
async def sds_trace(ods: str, endpoint: bool = False, **kwargs):
    """
    Function to get the SDS trace for an ODS code

    args:
    ods: str - the ODS code to trace
    endpoint: bool - whether to make an endpoint SDS trace

    returns:
    fhir bundle of the SDS trace
    """
    if endpoint:
        suffix = "Endpoint"
        partykey = kwargs.get("mhsparty")
        identifier = [
            "https://fhir.nhs.uk/Id/nhsServiceInteractionId|urn:nhs:names:services:gpconnect:fhir:operation:gpc.getstructuredrecord-1",
            f"https://fhir.nhs.uk/Id/nhsMhsPartyKey|{partykey}",
        ]

    else:
        suffix = "Device"
        # if identifier in kwargs, use that, otherwise use default
        if "identifiers" in kwargs:
            identifier = kwargs["identifiers"]
            print(f"Using custom identifiers: {identifier}")
        else:
            identifier = [
                # "https://fhir.nhs.uk/Id/nhsServiceInteractionId|urn:nhs:names:services:psis:REPC_IN150016UK05"
                "https://fhir.nhs.uk/Id/nhsServiceInteractionId|urn:nhs:names:services:gpconnect:fhir:operation:gpc.getstructuredrecord-1"
            ]

    url = f"{INT_BASE_PATH}spine-directory/FHIR/R4/{suffix}"
    organisation = f"https://fhir.nhs.uk/Id/ods-organization-code|{ods}"

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

    return json.loads(r.text)


async def get_self_asid():
    # looks up organisation's own ASID using the SDS trace endpoint
    ods = os.getenv("ORG_CODE")

    if not ods:
        raise Exception("ORG_CODE environment variable not set")

    prefix = "https://fhir.nhs.uk/Id/"
    "https://fhir.nhs.uk/Id/objectClass|nhsAs"
    parameters = {
        "nhsIDCode": ods,
        "objectClass": "nhsAs",
        "nhsAsSvcIAD": "urn:nhs:names:services:gpconnect:fhir:operation:gpc.getstructuredrecord-1",
        "nhsMhsManufacturerOrg": ods,
    }
    identifiers = [
        f"{prefix}nhsIDCode|{ods}",
        f"{prefix}objectClass|nhsAs",
    ]
    url = f"{PDS_PATH}spine-directory/FHIR/R4/Device"
    # r = httpx.get(url, params=parameters, identifiers=identifiers)
    asid_trace = await sds_trace(ods, endpoint=False)
    try:
        for item in (
            asid_trace.get("entry", [{}])[0].get("resource", {}).get("identifier", [])
        ):
            if item.get("system") == "https://fhir.nhs.uk/Id/nhsSpineASID":
                asid = item.get("value")
            elif item.get("system") == "https://fhir.nhs.uk/Id/nhsMhsPartyKey":
                nhsmhsparty = item.get("value")
    except Exception as e:
        msg = f"Unable to parse SDS trace response: {e}"
        raise Exception(msg)
    if asid:
        return asid
    else:
        raise Exception(f"ASID not found in SDS trace response: {asid_trace}")


if __name__ == "__main__":
    # patient = asyncio.run(lookup_patient(9658218873))
    # pprint.pprint(patient)

    # print(patient.gender)
    # print(patient.name[0].family)
    # print(patient.generalPractitioner[0].identifier.value)
    self_trace = asyncio.run(get_self_asid())
    pprint.pprint(self_trace)

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
