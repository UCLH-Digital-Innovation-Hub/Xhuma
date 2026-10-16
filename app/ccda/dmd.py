import json
import logging
import os
import pprint

import httpx

from ..redis_connect import snomed_client
from ..telemetry import measure, record_cache
from .models.datatypes import CD
from .models.dmd import DMDConcept
from .models.dmd import VPIProperty as VPI

client_id = os.getenv("DMD_CLIENT_ID")
client_secret = os.getenv("DMD_CLIENT_SECRET")


def _decode_cached_token(token: bytes | str | None) -> str | None:
    """Normalize cached token value from Redis into a plain string."""
    if token is None:
        return None
    if isinstance(token, bytes):
        return token.decode("utf-8")
    return token


async def get_terminology_token():
    """Fetch an access token from the DMD API using client credentials."""

    # check if client_id and client_secret are set
    if not client_id or not client_secret:
        logging.error("DMD_CLIENT_ID and DMD_CLIENT_SECRET must be set in environment variables.")
        raise ValueError("DMD_CLIENT_ID and DMD_CLIENT_SECRET must be set in environment variables.")

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
        with measure("terminology.token") as span:
            response = await client.post(url, data=data, headers=headers)
            span.set_attribute("http.response.status_code", response.status_code)
            response.raise_for_status()
        token_data = response.json()

        # cache the token for 30 minutes
        await snomed_client.setex("dmd_token", 30 * 60, token_data["access_token"])

        return token_data["access_token"]


ACTIVE_RELEASE_KEY = "dmd:active-release"
DMD_RESOLVED_TTL = int(os.getenv("DMD_RESOLVED_TTL", 9 * 24 * 3600))  # 9 days
DMD_ACTIVE_RELEASE_TTL = int(os.getenv("DMD_ACTIVE_RELEASE_TTL", 8 * 24 * 3600))  # 8 days


def dmd_cache_key(concept_id: int | str, properties: list = None) -> str:
    """Generate a legacy cache key for a DMD concept based on its ID and requested properties."""
    if properties:
        properties_key = ",".join(sorted(properties))
        return f"snomed:{concept_id}:properties:{properties_key}"
    return f"snomed:{concept_id}"


def dmd_raw_key(concept_id: int | str, release: str, properties: list = None) -> str:
    """Generate a versioned raw cache key for a DMD concept."""
    if properties:
        properties_key = ",".join(sorted(properties))
        return f"dmd:raw:v1:{release}:{concept_id}:properties:{properties_key}"
    return f"dmd:raw:v1:{release}:{concept_id}"


def dmd_resolved_key(concept_id: int | str, release: str) -> str:
    """Generate a versioned resolved cache key for a DMDConcept."""
    return f"dmd:resolved:v1:{release}:{concept_id}"


def _lookup_version(data: dict) -> str | None:
    """Extract release version from a FHIR Parameters $lookup response."""
    for p in data.get("parameter", []):
        name = p.get("name")
        if name in ("version", "system-version", "used-system"):
            val = p.get("valueString") or p.get("valueUri") or ""
            if "|" in val:
                _, _, ver = val.partition("|")
                if ver:
                    return ver
            if val:
                return val
        elif name == "system":
            val = p.get("valueUri") or p.get("valueString") or ""
            if "|" in val:
                _, _, ver = val.partition("|")
                if ver:
                    return ver
    return None


async def get_dmd_concept(concept_id: int | str, properties: list = None, release: str = None) -> dict:
    """Fetch concept data from cache or DMD API, optionally pinned to a specific release."""
    # Determine cache key based on whether release is specified
    if release:
        cache_key = dmd_raw_key(concept_id, release, properties)
    else:
        cache_key = dmd_cache_key(concept_id, properties)

    with measure("terminology.cache.read") as span:
        cached_concept = await snomed_client.get(cache_key)
        span.set_attribute("cache.hit", bool(cached_concept))
    record_cache("terminology", bool(cached_concept))

    if cached_concept:
        logging.info(f"Cache hit for SNOMED concept {concept_id} (key={cache_key})")
        return json.loads(cached_concept.decode("utf-8"))

    logging.info(f"Cache miss for SNOMED concept {concept_id}. Fetching from DMD API.")

    token = _decode_cached_token(await snomed_client.get("dmd_token"))
    if not token:
        logging.info("No cached DMD token found. Fetching new token.")
        token = await get_terminology_token()

    dmd_base_url = os.getenv("DMD_BASE_URL", "https://ontology.nhs.uk/production1/fhir")
    async with httpx.AsyncClient() as client:
        url = f"{dmd_base_url}/CodeSystem/$lookup?system=https://dmd.nhs.uk&code={concept_id}"
        if properties:
            for prop in properties:
                url += f"&property={prop}"
        if release:
            url += f"&version={release}"

        headers = {
            "Authorization": f"Bearer {token}",
        }
        with measure("terminology.http") as span:
            response = await client.get(url, headers=headers)
            span.set_attribute("http.response.status_code", response.status_code)

        if response.status_code == 401:
            logging.warning("Unauthorized access to DMD API. Token may have expired. Fetching new token.")
            token = await get_terminology_token()
            headers["Authorization"] = f"Bearer {token}"
            with measure("terminology.http", retry=True) as span:
                response = await client.get(url, headers=headers)
                span.set_attribute("http.response.status_code", response.status_code)
                response.raise_for_status()

        response.raise_for_status()
        concept_data = response.json()

        # Reject if response release mismatches requested release
        if release:
            resp_version = _lookup_version(concept_data)
            if resp_version and resp_version != release:
                raise ValueError(
                    f"DMD lookup returned release {resp_version} which does not match requested release {release}"
                )
            # cache for configured resolved TTL
            await snomed_client.setex(cache_key, DMD_RESOLVED_TTL, json.dumps(concept_data))
        else:
            # fallback cache for 7 days
            await snomed_client.setex(cache_key, 7 * 24 * 3600, json.dumps(concept_data))

        return concept_data


async def _resolve_dmd_concept(concept_id: int, release: str = None) -> DMDConcept:
    """Internal resolution of DMDConcept with release pinning on all dependent lookups."""
    properties = ["VPI", "ROUTECD", "parent"]
    dmd = await get_dmd_concept(concept_id, properties=properties, release=release)
    if not isinstance(dmd, dict):
        logging.error(f"Unexpected DMD concept data format for concept {concept_id}: {dmd}")
        dmd = json.loads(dmd)

    display_name = [prop["valueString"] for prop in dmd.get("parameter", []) if prop.get("name") == "display"]
    val_str = display_name[0] if display_name else ""

    processed_dmd = DMDConcept(
        concept_id=int(concept_id),
        valueString=val_str,
    )

    async def get_property(property_name: str, concept_data: dict) -> list:
        property_list = []
        for parm in concept_data.get("parameter", []):
            if parm.get("name") == "property":
                for part in parm.get("part", []):
                    if part.get("name") == "code" and part.get("valueCode") == property_name:
                        property_list.append(parm)
        return property_list

    async def get_subproperty(property_data: dict, subproperty_name: str) -> dict | None:
        for part in property_data.get("part", []):
            if part.get("name") == "subproperty":
                for subpart in part.get("part", []):
                    if subpart.get("name") == "code" and subpart.get("valueCode") == subproperty_name:
                        return part
        return None

    # Check parent property: if AMP, fetch parent VMP
    parents = await get_property("parent", dmd)
    if len(parents) == 2:
        value_codes = [parent["part"][1]["valueCode"] for parent in parents if len(parent.get("part", [])) > 1]
        if "AMP" in value_codes:
            value_codes.remove("AMP")
            if len(value_codes) == 1 and value_codes[0].isdigit():
                dmd = await get_dmd_concept(int(value_codes[0]), properties=properties, release=release)

    vpi_properties = await get_property("VPI", dmd)
    if len(vpi_properties) == 1:
        dose_value_part = await get_subproperty(vpi_properties[0], "STRNT_NMRTR_VAL")
        dose_value = None
        if dose_value_part:
            for subpart in dose_value_part.get("part", []):
                if subpart.get("name") == "valueDecimal":
                    dose_value = subpart.get("valueDecimal")

        dose_unit_part = await get_subproperty(vpi_properties[0], "STRNT_NMRTR_UOMCD")
        dose_unit_code = None
        if dose_unit_part:
            for subpart in dose_unit_part.get("part", []):
                if subpart.get("name") == "valueCoding":
                    dose_unit_code = subpart["valueCoding"].get("code")

        unit_display = None
        if dose_unit_code:
            unit_concept = await get_dmd_concept(dose_unit_code, release=release)
            unit_display_param = [parm for parm in unit_concept.get("parameter", []) if parm.get("name") == "display"]
            unit_display = unit_display_param[0].get("valueString") if unit_display_param else None

        processed_dmd.vpi = VPI(value=dose_value, unit=unit_display or "")

    route_properties = await get_property("ROUTECD", dmd)
    if len(route_properties) == 1:
        route_code = None
        for subpart in route_properties[0].get("part", []):
            if subpart.get("name") == "value":
                route_code = subpart.get("valueCoding", {}).get("code")

        if route_code:
            route_concept = await get_dmd_concept(route_code, release=release)
            route_display_param = [parm for parm in route_concept.get("parameter", []) if parm.get("name") == "display"]
            route_display = route_display_param[0].get("valueString") if route_display_param else None
            processed_dmd.route = CD(
                code=route_code,
                displayName=route_display,
                codeSystemName="https://dmd.nhs.uk",
            )

    return processed_dmd


async def dmd_lookup(concept_id: int | str) -> DMDConcept:
    """Lookup a dm+d concept, using active-release resolved cache where available."""
    # 1. Check if an active release is registered
    active_release_bytes = await snomed_client.get(ACTIVE_RELEASE_KEY)
    active_release = active_release_bytes.decode("utf-8") if active_release_bytes else None

    if active_release:
        key = dmd_resolved_key(concept_id, active_release)
        with measure("terminology.resolved.cache.read") as span:
            cached_resolved = await snomed_client.get(key)
            span.set_attribute("cache.hit", bool(cached_resolved))
        record_cache("terminology.resolved", bool(cached_resolved))

        if cached_resolved:
            logging.info(f"Resolved cache hit for concept {concept_id} under release {active_release}")
            return DMDConcept.model_validate_json(cached_resolved.decode("utf-8"))

        # On miss: resolve pinned to active_release and cache
        logging.info(f"Resolved cache miss for concept {concept_id}. Resolving pinned to release {active_release}.")
        result = await _resolve_dmd_concept(int(concept_id), release=active_release)
        await snomed_client.setex(key, DMD_RESOLVED_TTL, result.model_dump_json())
        return result

    # 2. No active release: safe existing on-demand fallback
    return await _resolve_dmd_concept(int(concept_id), release=None)


if __name__ == "__main__":
    import asyncio

    # Example usage
    async def main():
        # token = await get_terminology_token()
        # print(f"Access Token: {token}")

        concept_id = 38893711000001104  # Replace with a valid SNOMED concept ID
        # properties = ["*"]  # Fetch all properties
        # full_properties = await get_dmd_concept(concept_id, properties=properties)
        # pprint.pprint(full_properties)
        concept_term = await dmd_lookup(concept_id)
        pprint.pprint(concept_term)

    asyncio.run(main())
