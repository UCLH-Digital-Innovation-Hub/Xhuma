import logging
import os
import pprint

import httpx

from ..redis_connect import snomed_client
from .models.dmd import DMDConcept
from .models.dmd import VPIProperty as VPI

client_id = os.getenv("DMD_CLIENT_ID")
client_secret = os.getenv("DMD_CLIENT_SECRET")


async def get_terminology_token():
    """Fetch an access token from the DMD API using client credentials."""

    # check if client_id and client_secret are set
    if not client_id or not client_secret:
        logging.error(
            "DMD_CLIENT_ID and DMD_CLIENT_SECRET must be set in environment variables."
        )
        raise ValueError(
            "DMD_CLIENT_ID and DMD_CLIENT_SECRET must be set in environment variables."
        )

    url = "https://ontology.nhs.uk/authorisation/auth/realms/nhs-digital-terminology/protocol/openid-connect/token"

    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(url, data=data, headers=headers)
        response.raise_for_status()
        token_data = response.json()

        # cache the token for 5 minutes
        snomed_client.setex("dmd_token", 300, token_data["access_token"])

        return token_data["access_token"]


async def get_dmd_concept(concept_id: int, properties: list = None):
    # Check if the concept is in the cache
    cached_concept = snomed_client.get(f"snomed:{concept_id}")
    if cached_concept:
        logging.info(f"Cache hit for SNOMED concept {concept_id}")
        return cached_concept.decode("utf-8")

    logging.info(f"Cache miss for SNOMED concept {concept_id}. Fetching from DMD API.")
    # If not in cache, fetch from DMD API

    # check for cached token
    token = snomed_client.get("dmd_token")
    # if token is not cached, fetch a new one
    if not token:
        logging.info("No cached DMD token found. Fetching new token.")
        token = await get_terminology_token()

    async with httpx.AsyncClient() as client:

        url = f"https://ontology.nhs.uk/production1/fhir/CodeSystem/$lookup?system=https://dmd.nhs.uk&code={concept_id}"
        if properties:
            for prop in properties:
                url += f"&property={prop}"

        headers = {
            "Authorization": f"Bearer {token}",
        }
        response = await client.get(url, headers=headers)

        if response.status_code == 401:
            logging.warning(
                "Unauthorized access to DMD API. Token may have expired. Fetching new token."
            )
            token = await get_terminology_token()
            headers["Authorization"] = f"Bearer {token}"
            response = await client.get(url, headers=headers)
            response.raise_for_status()

        concept_data = response.json()

        # find vpi property
        # Cache the result for future use
        # snomed_client.setex(
        #     f"snomed:{concept_id}", 360, concept_data
        # )  # Cache for 1 hour

        return concept_data


async def dmd_lookup(concept_id: int) -> DMDConcept:
    properties = ["VPI", "ROUTECD"]
    dmd = await get_dmd_concept(concept_id, properties=properties)

    display_name = [
        prop["valueString"] for prop in dmd["parameter"] if prop["name"] == "display"
    ]

    processed_dmd = DMDConcept(
        concept_id=concept_id,
        valueString=display_name[0],
    )

    async def get_property(property_name: str, concept_data: dict) -> list:
        property_list = []
        for parm in concept_data["parameter"]:
            if parm["name"] == "property":
                for part in parm["part"]:
                    if part["name"] == "code" and part["valueCode"] == property_name:
                        property_list.append(parm)
        return property_list

    async def get_subproperty(property_data: dict, subproperty_name: str) -> dict:
        for part in property_data["part"]:
            if part["name"] == "subproperty":
                for subpart in part["part"]:
                    if (
                        subpart["name"] == "code"
                        and subpart["valueCode"] == subproperty_name
                    ):
                        return part

    vpi_properties = await get_property("VPI", dmd)
    logging.info(f"Found {len(vpi_properties)} VPI properties for concept {concept_id}")
    if len(vpi_properties) == 1:
        # single ingrediant so process
        dose_value_part = await get_subproperty(vpi_properties[0], "STRNT_NMRTR_VAL")
        dose_value = None
        for subpart in dose_value_part["part"]:
            if subpart["name"] == "valueDecimal":
                dose_value = subpart["valueDecimal"]
        dose_unit_part = await get_subproperty(vpi_properties[0], "STRNT_NMRTR_UOMCD")
        dose_unit_code = None
        for subpart in dose_unit_part["part"]:
            if subpart["name"] == "valueCoding":
                dose_unit_code = subpart["valueCoding"]["code"]
    if dose_unit_code:
        # lookup the unit code in SNOMED to get the display name
        unit_concept = await get_dmd_concept(dose_unit_code)
        # print(unit_concept)
        unit_display_parameter = [
            parm for parm in unit_concept["parameter"] if parm["name"] == "display"
        ]
        # print(unit_display_parameter)
        unit_display = (
            unit_display_parameter[0]["valueString"] if unit_display_parameter else None
        )

    # pprint.pprint(unit_concept)
    # print(dose_unit_code)
    # print(unit_display)
    # print(dose_value)

    processed_dmd.vpi = VPI(value=dose_value, unit=unit_display)
    return processed_dmd


if __name__ == "__main__":
    import asyncio

    # Example usage
    async def main():
        # token = await get_terminology_token()
        # print(f"Access Token: {token}")

        concept_id = 42370611000001103  # Replace with a valid SNOMED concept ID
        concept_term = await dmd_lookup(concept_id)
        pprint.pprint(concept_term)
        print(concept_term.vpi.value)
        print(concept_term.vpi.unit)

    asyncio.run(main())
