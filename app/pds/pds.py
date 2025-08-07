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
        return nhs_token

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
        "NHSD-End-User-Organisation-ODS": "Y12345",
        "Authorization": f"Bearer {nhs_token}",
        "accept": "application/fhir+json",
    }

    url = f"{INT_BASE_PATH}personal-demographics/FHIR/R4/Patient/{nhsno}"
    # print(url)

    r = httpx.get(url, headers=headers)
    if r.status_code != 200:
        raise Exception(f"{r.status_code}: {r.text}")
        # print(r.text)

    # if 401, get new one and try again
    if r.status_code == 401:
        print("401: trying to refresh token")
        nhs_token = get_pds_token()
        headers = {
            "X-Request-ID": str(uuid.uuid4()),
            "X-Correlation-ID": str(uuid.uuid4()),
            "NHSD-End-User-Organisation-ODS": "Y12345",
            "Authorization": f"Bearer {nhs_token}",
            "accept": "application/fhir+json",
        }
        r = httpx.get(url, headers=headers)

    if r.status_code != 200:
        # raise Exception(f"{r.status_code}: {r.text}")
        # mocking response whilst PDS INT is down
        print("mocking response")
        patient_dict = {
            "address": [
                {
                    "extension": [
                        {
                            "extension": [
                                {
                                    "url": "type",
                                    "valueCoding": {
                                        "code": "PAF",
                                        "system": "https://fhir.hl7.org.uk/CodeSystem/UKCore-AddressKeyType",
                                    },
                                },
                                {"url": "value", "valueString": "19343715"},
                            ],
                            "url": "https://fhir.hl7.org.uk/StructureDefinition/Extension-UKCore-AddressKey",
                        }
                    ],
                    "id": "XUSFS",
                    "line": ["268 PIPER KNOWLE ROAD", "STOCKTON-ON-TEES", "CLEVELAND"],
                    "period": {"start": "1998-07-04"},
                    "postalCode": "TS19 8JP",
                    "use": "home",
                }
            ],
            "birthDate": "1938-12-11",
            "extension": [
                {
                    "extension": [
                        {
                            "url": "language",
                            "valueCodeableConcept": {
                                "coding": [
                                    {
                                        "code": "de",
                                        "display": "German",
                                        "system": "https://fhir.hl7.org.uk/CodeSystem/UKCore-HumanLanguage",
                                        "version": "1.0.0",
                                    }
                                ]
                            },
                        },
                        {"url": "interpreterRequired", "valueBoolean": True},
                    ],
                    "url": "https://fhir.hl7.org.uk/StructureDefinition/Extension-UKCore-NHSCommunication",
                }
            ],
            "gender": "male",
            "generalPractitioner": [
                {
                    "id": "BZYbh",
                    "identifier": {
                        "period": {"start": "2023-09-29"},
                        "system": "https://fhir.nhs.uk/Id/ods-organization-code",
                        "value": "B83621",
                    },
                    "type": "Organization",
                }
            ],
            "id": "9690937278",
            "identifier": [
                {
                    "extension": [
                        {
                            "url": "https://fhir.hl7.org.uk/StructureDefinition/Extension-UKCore-NHSNumberVerificationStatus",
                            "valueCodeableConcept": {
                                "coding": [
                                    {
                                        "code": "01",
                                        "display": "Number present and verified",
                                        "system": "https://fhir.hl7.org.uk/CodeSystem/UKCore-NHSNumberVerificationStatus",
                                        "version": "1.0.0",
                                    }
                                ]
                            },
                        }
                    ],
                    "system": "https://fhir.nhs.uk/Id/nhs-number",
                    "value": "9690937278",
                }
            ],
            "meta": {
                "security": [
                    {
                        "code": "U",
                        "display": "unrestricted",
                        "system": "http://terminology.hl7.org/CodeSystem/v3-Confidentiality",
                    }
                ],
                "versionId": "4",
            },
            "name": [
                {
                    "family": "SAMUAL",
                    "given": ["Lucien", "Keith"],
                    "id": "LVxyI",
                    "period": {"start": "1970-08-11"},
                    "prefix": ["MR"],
                    "use": "usual",
                }
            ],
            "resourceType": "Patient",
            "telecom": [
                {
                    "id": "EAFDAA6A",
                    "period": {"start": "2024-11-27"},
                    "system": "email",
                    "use": "home",
                    "value": "luclen@gmail.com",
                }
            ],
        }
    else:
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
