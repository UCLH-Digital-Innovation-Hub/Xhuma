import asyncio
import json
import os
import uuid
from datetime import timedelta

import fastapi
import httpx
from fhirclient.models import patient as p

from app.redis_connect import redis_client
from app.security import pds_jwt

BASE_PATH = "https://sandbox.api.service.nhs.uk/"
DEV_BASE_PATH = "https://dev.api.service.nhs.uk/"
INT_BASE_PATH = "https://int.api.service.nhs.uk/"
API_KEY = os.getenv("API_KEY")

router = fastapi.APIRouter(prefix="/pds")


@router.get("/lookup_patient/{nhsno}")
async def lookup_patient(nhsno: int):
    def get_pds_token():
        full_path = f"{INT_BASE_PATH}oauth2/token"
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

    # if nhs token expired or not request, get one and cache
    if not redis_client.exists("access_token"):
        nhs_token = get_pds_token()
    else:
        nhs_token = redis_client.get("access_token").decode("utf-8")

    # print(f"nhs_token: {nhs_token}")
    # set headers for pds request
    headers = {
        "X-Request-ID": str(uuid.uuid4()),
        "X-Correlation-ID": str(uuid.uuid4()),
        # TODO make end user organisation dynamic
        "NHSD-End-User-Organisation-ODS": "RVV00",
        "Authorization": f"Bearer {nhs_token}",
        "accept": "application/fhir+json",
    }

    url = f"{INT_BASE_PATH}personal-demographics/FHIR/R4/Patient/{nhsno}"

    r = httpx.get(url, headers=headers)
    if r.status_code != 200:
        # print(r.text)
        # raise Exception(f"{r.status_code}: {r.text}")
        return None

    patient_dict = json.loads(r.text)

    return patient_dict


@router.get("/sds/{ods}")
async def sds_trace(ods: str):
    url = f"{INT_BASE_PATH}spine-directory/FHIR/R4/Device"
    organisation = f"https://fhir.nhs.uk/Id/ods-organization-code|{ods}"
    identifier = [
        "https://fhir.nhs.uk/Id/nhsServiceInteractionId|urn:nhs:names:services:psis:REPC_IN150016UK05"
    ]
    api_key = os.environ.get("API_KEY")
    parameters = {
        "organization": organisation,
        "identifier": identifier,
    }
    headers = {
        "X-Request-ID": str(uuid.uuid4()),
        "accept": "application/fhir+json",
        "apikey": api_key,
    }
    r = httpx.get(url, headers=headers, params=parameters)

    return json.loads(r.text)


if __name__ == "__main__":

    patient = asyncio.run(lookup_patient(9690937278))

    # print(patient.gender)
    # print(patient.name[0].family)
    # print(patient.generalPractitioner[0].identifier.value)

    ods = asyncio.run(sds_trace("RVV00"))
    print(ods)
