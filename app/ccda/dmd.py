import json
import logging
import os
import pprint
import time

import httpx

from ..redis_connect import snomed_client
from .models.datatypes import CD
from .models.dmd import DMDConcept
from .models.dmd import VPIProperty as VPI

client_id = os.getenv("DMD_CLIENT_ID")
client_secret = os.getenv("DMD_CLIENT_SECRET")

_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)


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

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        logging.info(f"POST {url}")
        t0 = time.perf_counter()
        response = await client.post(url, data=data, headers=headers)
        logging.info(
            f"POST {url} -> {response.status_code} ({time.perf_counter() - t0:.2f}s)"
        )
        if not response.is_success:
            logging.error(f"Token request failed: {response.text}")
        response.raise_for_status()
        token_data = response.json()

        # cache the token for 5 minutes
        snomed_client.setex("dmd_token", 300, token_data["access_token"])

        return token_data["access_token"]


async def _get_token() -> str:
    """Return a valid bearer token, fetching a new one if the cache is empty."""
    cached = snomed_client.get("dmd_token")
    if cached:
        return cached.decode("utf-8")
    return await get_terminology_token()


def dmd_cache_key(concept_id: int, properties: list = None) -> str:
    """Generate a cache key for a DMD concept based on its ID and requested properties."""
    if properties:
        properties_key = ",".join(sorted(properties))
        return f"snomed:{concept_id}:properties:{properties_key}"
    return f"snomed:{concept_id}"


async def get_dmd_concept(concept_id: int, properties: list = None) -> dict:
    # Check if the concept is in the cache
    cache_key = dmd_cache_key(concept_id, properties)
    cached_concept = snomed_client.get(cache_key)
    if cached_concept:
        logging.info(f"Cache hit for SNOMED concept {concept_id}")
        # cached concept is stored as json string, decode it before returning
        return json.loads(cached_concept.decode("utf-8"))

    logging.info(f"Cache miss for SNOMED concept {concept_id}. Fetching from DMD API.")
    # If not in cache, fetch from DMD API

    token = await _get_token()

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        url = f"https://ontology.nhs.uk/production1/fhir/CodeSystem/$lookup?system=https://dmd.nhs.uk&code={concept_id}"
        if properties:
            for prop in properties:
                url += f"&property={prop}"

        headers = {
            "Authorization": f"Bearer {token}",
        }
        logging.info(f"GET {url}")
        t0 = time.perf_counter()
        response = await client.get(url, headers=headers)
        logging.info(
            f"GET {url} -> {response.status_code} ({time.perf_counter() - t0:.2f}s)"
        )

        if response.status_code == 401:
            logging.warning(
                "Unauthorized access to DMD API. Token may have expired. Fetching new token."
            )
            token = await get_terminology_token()
            headers["Authorization"] = f"Bearer {token}"
            logging.info(f"GET {url} (retry)")
            t0 = time.perf_counter()
            response = await client.get(url, headers=headers)
            logging.info(
                f"GET {url} (retry) -> {response.status_code} ({time.perf_counter() - t0:.2f}s)"
            )
            if not response.is_success:
                logging.error(
                    f"Concept request failed after token refresh: {response.text}"
                )
            response.raise_for_status()

        concept_data = response.json()
        # cache the concept data for 1 week
        snomed_client.setex(cache_key, 7 * 24 * 3600, json.dumps(concept_data))
        # print(f"Cached DMD concept {concept_id} with properties {properties} under key {cache_key}")

        return concept_data


async def _populate_parent_cache(parent: str) -> int:
    """Expand a DMD ValueSet filtered by parent and cache every concept's display name."""

    token = await _get_token()

    url = "https://ontology.nhs.uk/production1/fhir/ValueSet/$expand"
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "valueSet",
                "resource": {
                    "resourceType": "ValueSet",
                    "compose": {
                        "include": [
                            {
                                "system": "https://dmd.nhs.uk",
                                "filter": [
                                    {
                                        "property": "parent",
                                        "op": "=",
                                        "value": parent,
                                    }
                                ],
                            }
                        ]
                    },
                },
            }
        ],
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/fhir+json",
    }

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        logging.info(f"POST {url} (parent={parent})")
        t0 = time.perf_counter()
        response = await client.post(url, json=body, headers=headers)
        logging.info(
            f"POST {url} (parent={parent}) -> {response.status_code} ({time.perf_counter() - t0:.2f}s)"
        )

        if response.status_code == 401:
            logging.warning(
                f"Token expired expanding {parent} ValueSet. Fetching new token."
            )
            token = await get_terminology_token()
            headers["Authorization"] = f"Bearer {token}"
            logging.info(f"POST {url} (parent={parent}, retry)")
            t0 = time.perf_counter()
            response = await client.post(url, json=body, headers=headers)
            logging.info(
                f"POST {url} (parent={parent}, retry) -> {response.status_code} ({time.perf_counter() - t0:.2f}s)"
            )

        if not response.is_success:
            logging.error(f"{parent} ValueSet expand failed: {response.text}")
        response.raise_for_status()

        concepts = response.json().get("expansion", {}).get("contains", [])

    count = 0
    for concept in concepts:
        code = concept.get("code")
        display = concept.get("display")
        if not code or not display:
            continue
        cached = {"parameter": [{"name": "display", "valueString": display}]}
        snomed_client.setex(dmd_cache_key(code), 7 * 24 * 3600, json.dumps(cached))
        count += 1

    logging.info(f"Cached {count} DMD {parent} concepts")
    return count


async def populate_route_cache() -> int:
    """Pre-populate the Redis cache with all DMD route concepts."""
    return await _populate_parent_cache("ROUTE")


async def populate_uom_cache() -> int:
    """Pre-populate the Redis cache with all DMD unit-of-measure concepts."""
    return await _populate_parent_cache("UOM")


_VALUE_KEYS = (
    "valueCode",
    "valueCoding",
    "valueDecimal",
    "valueString",
    "valueBoolean",
    "valueInteger",
    "valueDateTime",
)

# The NHS Digital FHIR R4 server backports R5 expansion properties via this extension URL.
_PROP_EXT_URL = "http://hl7.org/fhir/5.0/StructureDefinition/extension-ValueSet.expansion.contains.property"


def _expansion_concept_to_params(concept: dict) -> dict:
    """Transform a ValueSet expansion concept into the Parameters $lookup response format.

    The server returns properties as R5-backported extensions rather than native R4
    property elements. This converts that extension structure into the Parameters format
    that get_dmd_concept caches and dmd_lookup reads.
    """
    params = [{"name": "display", "valueString": concept.get("display", "")}]

    for ext in concept.get("extension", []):
        if ext.get("url") != _PROP_EXT_URL:
            continue

        inner = ext.get("extension", [])

        code = next((e["valueCode"] for e in inner if e.get("url") == "code"), None)
        if not code:
            continue

        part = [{"name": "code", "valueCode": code}]

        for item in inner:
            url = item.get("url")
            if url == "code":
                continue
            elif url == "value":
                for vk in _VALUE_KEYS:
                    if vk in item:
                        part.append({"name": "value", vk: item[vk]})
                        break
            elif url == "subproperty":
                subinner = item.get("extension", [])
                subpart = []
                for subitem in subinner:
                    suburl = subitem.get("url")
                    if suburl == "code":
                        subpart.append(
                            {"name": "code", "valueCode": subitem["valueCode"]}
                        )
                    elif suburl == "value":
                        for vk in _VALUE_KEYS:
                            if vk in subitem:
                                # subproperty values use the type as the part name
                                subpart.append({"name": vk, vk: subitem[vk]})
                                break
                part.append({"name": "subproperty", "part": subpart})

        params.append({"name": "property", "part": part})

    return {"parameter": params}


_CONCEPT_PROPERTIES = ["VPI", "ROUTECD", "parent"]


async def _populate_concept_cache(parent: str) -> int:
    """Expand a DMD ValueSet with VPI/ROUTECD/parent properties and pre-populate the Redis cache.

    Caches each concept under two keys so both get_dmd_concept(code) and
    get_dmd_concept(code, properties=["VPI","ROUTECD","parent"]) are cache hits.
    """
    token = await _get_token()

    url = "https://ontology.nhs.uk/production1/fhir/ValueSet/$expand"
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "valueSet",
                "resource": {
                    "resourceType": "ValueSet",
                    "compose": {
                        "include": [
                            {
                                "system": "https://dmd.nhs.uk",
                                "filter": [
                                    {
                                        "property": "parent",
                                        "op": "=",
                                        "value": parent,
                                    }
                                ],
                            }
                        ]
                    },
                },
            },
            {"name": "property", "valueString": "VPI"},
            {"name": "property", "valueString": "parent"},
            {"name": "property", "valueString": "ROUTECD"},
        ],
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/fhir+json",
    }

    PAGE_SIZE = 10000
    all_concepts = []
    offset = 0
    page_num = 0

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        while True:
            page_num += 1
            paged_body = {
                **body,
                "parameter": body["parameter"]
                + [
                    {"name": "count", "valueInteger": PAGE_SIZE},
                    {"name": "offset", "valueInteger": offset},
                ],
            }

            logging.info(
                f"POST {url} (parent={parent}, page={page_num}, offset={offset})"
            )
            t0 = time.perf_counter()
            response = await client.post(url, json=paged_body, headers=headers)
            elapsed = time.perf_counter() - t0
            logging.info(
                f"POST {url} (parent={parent}, page={page_num}) -> {response.status_code} ({elapsed:.2f}s)"
            )

            if response.status_code == 401:
                logging.warning(
                    f"Token expired expanding {parent} ValueSet. Fetching new token."
                )
                token = await get_terminology_token()
                headers["Authorization"] = f"Bearer {token}"
                logging.info(f"POST {url} (parent={parent}, page={page_num}, retry)")
                t0 = time.perf_counter()
                response = await client.post(url, json=paged_body, headers=headers)
                elapsed = time.perf_counter() - t0
                logging.info(
                    f"POST {url} (parent={parent}, page={page_num}, retry) -> {response.status_code} ({elapsed:.2f}s)"
                )

            if not response.is_success:
                logging.error(f"{parent} ValueSet expand failed: {response.text}")
            response.raise_for_status()

            expansion = response.json().get("expansion", {})
            page = expansion.get("contains", [])
            all_concepts.extend(page)

            total = expansion.get("total", len(all_concepts))
            logging.info(
                f"{parent} page {page_num} complete: {len(page)} concepts "
                f"({len(all_concepts)}/{total} total)"
            )

            if len(all_concepts) >= total or not page:
                break
            offset += PAGE_SIZE

    TTL = 7 * 24 * 3600
    count = 0
    for concept in all_concepts:
        code = concept.get("code")
        display = concept.get("display")
        if not code or not display:
            continue

        params = _expansion_concept_to_params(concept)

        snomed_client.setex(
            dmd_cache_key(code, _CONCEPT_PROPERTIES), TTL, json.dumps(params)
        )
        snomed_client.setex(
            dmd_cache_key(code),
            TTL,
            json.dumps({"parameter": [{"name": "display", "valueString": display}]}),
        )
        count += 1

    logging.info(f"Cached {count} DMD {parent} concepts")
    return count


async def populate_vmp_cache() -> int:
    """Pre-populate the Redis cache with all DMD VMP concepts."""
    return await _populate_concept_cache("VMP")


async def populate_amp_cache() -> int:
    """Pre-populate the Redis cache with all DMD AMP concepts."""
    return await _populate_concept_cache("AMP")


async def dmd_lookup(concept_id: int) -> DMDConcept:
    properties = ["VPI", "ROUTECD", "parent"]
    dmd = await get_dmd_concept(concept_id, properties=properties)
    # make sure dmd is a dict
    if not isinstance(dmd, dict):
        logging.error(
            f"Unexpected DMD concept data format for concept {concept_id}: {dmd}"
        )
        dmd = json.loads(dmd)

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

    # check if there is a parent property
    parents = await get_property("parent", dmd)
    if len(parents) == 2:
        # if there is AMP and code
        value_codes = [parent["part"][1]["valueCode"] for parent in parents]
        if "AMP" in value_codes:
            # pop the AMP parent as we don't want to process it
            amp_index = value_codes.index("AMP")
            value_codes.pop(amp_index)

            # check remaining parent is an int
            if len(value_codes) == 1 and value_codes[0].isdigit():
                dmd = await get_dmd_concept(int(value_codes[0]), properties=properties)

    vpi_properties = await get_property("VPI", dmd)
    # logging.info(f"Found {len(vpi_properties)} VPI properties for concept {concept_id}")
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
            # pprint.pprint(unit_concept)
            # print(type(unit_concept))
            unit_display_parameter = [
                parm for parm in unit_concept["parameter"] if parm["name"] == "display"
            ]
            unit_display = (
                unit_display_parameter[0]["valueString"]
                if unit_display_parameter
                else None
            )

        processed_dmd.vpi = VPI(value=dose_value, unit=unit_display)

    # look for routeCD property
    route_properties = await get_property("ROUTECD", dmd)
    # logging.info(
    #     f"Found {len(route_properties)} ROUTECD properties for concept {concept_id}"
    # )
    # pprint.pprint(route_properties)
    if len(route_properties) == 1:
        route_code = None
        for subpart in route_properties[0]["part"]:
            if subpart["name"] == "value":
                route_code = subpart["valueCoding"]["code"]
        if route_code:
            # lookup the route code in SNOMED to get the display name
            route_concept = await get_dmd_concept(route_code)
            # print(f"Route concept for code {route_code}:")
            # pprint.pprint(route_concept)
            route_display_parameter = [
                parm for parm in route_concept["parameter"] if parm["name"] == "display"
            ]
            route_display = (
                route_display_parameter[0]["valueString"]
                if route_display_parameter
                else None
            )
            processed_dmd.route = CD(
                code=route_code,
                displayName=route_display,
                codeSystemName="https://dmd.nhs.uk",  # Assuming the code system is DMD, OID will be snomed as dmd is subset
            )

    return processed_dmd


if __name__ == "__main__":
    import asyncio

    logging.basicConfig(level=logging.INFO)

    async def main():
        start = time.perf_counter()
        route_count = await populate_route_cache()
        uom_count = await populate_uom_cache()
        vmp_count = await populate_vmp_cache()
        amp_count = await populate_amp_cache()
        elapsed = time.perf_counter() - start
        print(
            f"Pre-populated {route_count} routes, {uom_count} UOMs, "
            f"{vmp_count} VMPs, {amp_count} AMPs in {elapsed:.1f}s"
        )

        concept_id = 38893711000001104  # Replace with a valid SNOMED concept ID
        # properties = ["*"]  # Fetch all properties
        # full_properties = await get_dmd_concept(concept_id, properties=properties)
        # pprint.pprint(full_properties)
        concept_term = await dmd_lookup(concept_id)
        pprint.pprint(concept_term)

    asyncio.run(main())
