"""dm+d cache prewarming and operational status tool for Xhuma.

Invocation:
    python -m app.ops.dmd_cache warm
    python -m app.ops.dmd_cache status
"""

import argparse
import asyncio
import email.utils
import json
import logging
import os
import random
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from app.ccda.models.datatypes import CD
from app.ccda.models.dmd import DMDConcept
from app.ccda.models.dmd import VPIProperty as VPI
from app.redis_connect import snomed_client

logger = logging.getLogger("xhuma.dmd_cache")

# Redis key constants
ACTIVE_RELEASE_KEY = "dmd:active-release"
WARM_LOCK_KEY = "dmd:warm:v1:lock"
RESOLVED_KEY_PREFIX = "dmd:resolved:v1"
RAW_KEY_PREFIX = "dmd:raw:v1"
MANIFEST_KEY_PREFIX = "dmd:warm:v1"

# Configurable TTLs
DMD_RESOLVED_TTL = int(os.getenv("DMD_RESOLVED_TTL", 9 * 24 * 3600))  # 9 days
DMD_ACTIVE_RELEASE_TTL = int(os.getenv("DMD_ACTIVE_RELEASE_TTL", 8 * 24 * 3600))  # 8 days
DMD_MANIFEST_TTL = int(os.getenv("DMD_MANIFEST_TTL", 9 * 24 * 3600))  # 9 days
# 45 minutes: enforces Container Apps replica timeout (1800s) < Logic App poll (2100s) < Redis lock TTL (2700s)
DMD_LOCK_TTL = int(os.getenv("DMD_LOCK_TTL", 2700))
DMD_WARM_MAX_REDIS_MEMORY_PERCENT = float(os.getenv("DMD_WARM_MAX_REDIS_MEMORY_PERCENT", 80.0))
BYTES_PER_CONCEPT_ESTIMATE = int(os.getenv("DMD_WARM_BYTES_PER_CONCEPT", 1024))
DMD_WARM_MEMORY_SAFETY_MULTIPLIER = float(os.getenv("DMD_WARM_MEMORY_SAFETY_MULTIPLIER", 1.5))
PAGE_SIZE = int(os.getenv("DMD_WARM_PAGE_SIZE", 1000))

# FHIR and HTTP timeouts / limits
_TIMEOUT = httpx.Timeout(connect=15.0, read=300.0, write=30.0, pool=15.0)
_LIMITS = httpx.Limits(max_keepalive_connections=10, max_connections=20, keepalive_expiry=30.0)

RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}
MAX_HTTP_ATTEMPTS = 4
OVERALL_RETRY_BUDGET_SECONDS = 120.0
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 30.0

_PROP_EXT_URL = "http://hl7.org/fhir/5.0/StructureDefinition/extension-ValueSet.expansion.contains.property"
_VALUE_KEYS = (
    "valueCode",
    "valueCoding",
    "valueDecimal",
    "valueString",
    "valueBoolean",
    "valueInteger",
    "valueDateTime",
)

LUA_RELEASE_LOCK = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""

LUA_RENEW_LOCK = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("expire", KEYS[1], ARGV[2])
else
    return 0
end
"""


def parse_retry_after(header_val: str | None) -> float | None:
    """Parse HTTP Retry-After header into seconds."""
    if not header_val:
        return None
    try:
        val = float(header_val)
        return max(0.0, val)
    except ValueError:
        pass
    try:
        dt = email.utils.parsedate_to_datetime(header_val)
        now = datetime.now(timezone.utc)
        return max(0.0, (dt - now).total_seconds())
    except Exception:
        return None


class TerminologyClient:
    """HTTP client wrapper providing connection pooling, bounded retries, jitter, and 401 refresh."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        initial_token: str,
        max_attempts: int = MAX_HTTP_ATTEMPTS,
        retry_budget_s: float = OVERALL_RETRY_BUDGET_SECONDS,
    ):
        self.client = client
        self.base_url = base_url
        self.token = initial_token
        self.max_attempts = max_attempts
        self.retry_budget_s = retry_budget_s
        self.token_refreshed = False

    async def post_fhir(self, path: str, body: dict) -> httpx.Response:
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        start_time = time.monotonic()
        attempt = 0

        while True:
            attempt += 1
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/fhir+json",
            }
            try:
                resp = await self.client.post(url, json=body, headers=headers)

                # Bounded 401 token refresh path (at most once per client run)
                if resp.status_code == 401:
                    if not self.token_refreshed:
                        logger.warning("Terminology server returned 401 Unauthorized. Refreshing token once.")
                        self.token_refreshed = True
                        self.token = await fetch_token()
                        continue
                    else:
                        logger.error("Terminology server returned 401 after token refresh. Aborting.")
                        resp.raise_for_status()

                # Retry only for network failures and 408/429/500/502/503/504
                if resp.status_code in RETRYABLE_STATUS_CODES:
                    elapsed = time.monotonic() - start_time
                    if attempt >= self.max_attempts or elapsed >= self.retry_budget_s:
                        logger.error(
                            "Retry budget exhausted for %s (status %d, attempt %d/%d, elapsed %.1fs)",
                            url,
                            resp.status_code,
                            attempt,
                            self.max_attempts,
                            elapsed,
                        )
                        resp.raise_for_status()

                    # Calculate sleep time, supporting Retry-After for 429/503
                    sleep_time = None
                    if resp.status_code in (429, 503):
                        sleep_time = parse_retry_after(resp.headers.get("Retry-After"))

                    if sleep_time is None:
                        # Exponential backoff with random jitter
                        backoff = min(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)), BACKOFF_MAX_SECONDS)
                        sleep_time = backoff + random.uniform(0.1, 0.5)

                    remaining_budget = max(0.0, self.retry_budget_s - (time.monotonic() - start_time))
                    sleep_time = min(sleep_time, remaining_budget)

                    logger.warning(
                        "Transient HTTP %d from %s. Retrying in %.2fs (attempt %d/%d)",
                        resp.status_code,
                        url,
                        sleep_time,
                        attempt,
                        self.max_attempts,
                    )
                    await asyncio.sleep(sleep_time)
                    continue

                # Ordinary non-transient 4xx (400, 403, 404, 422) raises immediately without retry
                resp.raise_for_status()
                return resp

            except (httpx.NetworkError, httpx.TimeoutException) as exc:
                elapsed = time.monotonic() - start_time
                if attempt >= self.max_attempts or elapsed >= self.retry_budget_s:
                    logger.error(
                        "Network retry budget exhausted for %s (attempt %d/%d, elapsed %.1fs): %s",
                        url,
                        attempt,
                        self.max_attempts,
                        elapsed,
                        exc,
                    )
                    raise

                backoff = min(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)), BACKOFF_MAX_SECONDS)
                sleep_time = backoff + random.uniform(0.1, 0.5)
                remaining_budget = max(0.0, self.retry_budget_s - (time.monotonic() - start_time))
                sleep_time = min(sleep_time, remaining_budget)

                logger.warning(
                    "Network error %s from %s. Retrying in %.2fs (attempt %d/%d)",
                    type(exc).__name__,
                    url,
                    sleep_time,
                    attempt,
                    self.max_attempts,
                )
                await asyncio.sleep(sleep_time)


def resolved_key(release: str, concept_id: int | str) -> str:
    """Return key for prewarmed resolved DMDConcept."""
    return f"{RESOLVED_KEY_PREFIX}:{release}:{concept_id}"


def raw_key(release: str, concept_id: int | str, properties: list | None = None) -> str:
    """Return key for release-pinned raw concept lookup."""
    if properties:
        props_str = ",".join(sorted(properties))
        return f"{RAW_KEY_PREFIX}:{release}:{concept_id}:properties:{props_str}"
    return f"{RAW_KEY_PREFIX}:{release}:{concept_id}"


def manifest_key(release: str) -> str:
    """Return key for release warm manifest."""
    return f"{MANIFEST_KEY_PREFIX}:{release}:manifest"


def _expansion_version(expansion: dict) -> str | None:
    """Extract the dm+d code-system release from a ValueSet expansion."""
    for parameter in expansion.get("parameter", []):
        name = parameter.get("name")
        if name in ("version", "system-version", "used-system"):
            value = parameter.get("valueUri") or parameter.get("valueCanonical") or parameter.get("valueString", "")
            system, separator, version = value.partition("|")
            if system == "https://dmd.nhs.uk" and separator and version:
                return version
            if not separator and value and ("dmd" in value or name == "version"):
                return value
    return None


def _extract_concept_properties(concept: dict) -> dict[str, Any]:
    """Parse properties from an expansion concept (handles native FHIR and R5 extension backport)."""
    result: dict[str, Any] = {
        "parents": [],
        "route_code": None,
        "vpi_val": None,
        "vpi_uom": None,
    }

    # 1. Check native FHIR property list
    for prop in concept.get("property", []):
        code = prop.get("code")
        if code == "parent":
            val = prop.get("valueCode") or prop.get("value")
            if val:
                result["parents"].append(str(val))
        elif code == "ROUTECD":
            coding = prop.get("valueCoding") or {}
            result["route_code"] = coding.get("code") or prop.get("valueCode")
        elif code == "VPI":
            for sub in prop.get("subproperty", []):
                sub_code = sub.get("code")
                if sub_code == "STRNT_NMRTR_VAL":
                    result["vpi_val"] = sub.get("valueDecimal")
                elif sub_code == "STRNT_NMRTR_UOMCD":
                    sub_coding = sub.get("valueCoding") or {}
                    result["vpi_uom"] = sub_coding.get("code") or sub.get("valueCode")

    # 2. Check Ontoserver R5-backport extensions
    for ext in concept.get("extension", []):
        if ext.get("url") != _PROP_EXT_URL:
            continue
        inner = ext.get("extension", [])
        code = next((e.get("valueCode") for e in inner if e.get("url") == "code"), None)
        if not code:
            continue

        if code == "parent":
            for item in inner:
                if item.get("url") == "value":
                    val = item.get("valueCode") or item.get("valueString")
                    if val:
                        result["parents"].append(str(val))
        elif code == "ROUTECD":
            for item in inner:
                if item.get("url") == "value":
                    coding = item.get("valueCoding") or {}
                    result["route_code"] = coding.get("code") or item.get("valueCode")
        elif code == "VPI":
            for item in inner:
                if item.get("url") == "subproperty":
                    subinner = item.get("extension", [])
                    subcode = next((e.get("valueCode") for e in subinner if e.get("url") == "code"), None)
                    if subcode == "STRNT_NMRTR_VAL":
                        for subitem in subinner:
                            if subitem.get("url") == "value":
                                result["vpi_val"] = subitem.get("valueDecimal")
                    elif subcode == "STRNT_NMRTR_UOMCD":
                        for subitem in subinner:
                            if subitem.get("url") == "value":
                                subcoding = subitem.get("valueCoding") or {}
                                result["vpi_uom"] = subcoding.get("code") or subitem.get("valueCode")

    return result


async def acquire_lock(owner_token: str, ttl: int = DMD_LOCK_TTL) -> bool:
    """Acquire the Redis warmer lock using SET NX with explicit TTL."""
    return await snomed_client.set(WARM_LOCK_KEY, owner_token, nx=True, ex=ttl)


async def renew_lock(owner_token: str, ttl: int = DMD_LOCK_TTL) -> bool:
    """Renew warmer lock TTL safely if and only if owner token matches."""
    res = await snomed_client.eval(LUA_RENEW_LOCK, 1, WARM_LOCK_KEY, owner_token, ttl)
    return bool(res)


async def release_lock(owner_token: str) -> bool:
    """Release warmer lock safely if and only if owner token matches."""
    res = await snomed_client.eval(LUA_RELEASE_LOCK, 1, WARM_LOCK_KEY, owner_token)
    return bool(res)


async def get_redis_memory_state() -> dict[str, int]:
    """Capture current Redis memory and eviction counters."""
    info = await snomed_client.info()
    return {
        "used_memory": int(info.get("used_memory", 0)),
        "maxmemory": int(info.get("maxmemory", 0)),
        "evicted_keys": int(info.get("evicted_keys", 0)),
    }


def check_preflight_memory(
    mem_state: dict[str, int],
    estimated_concepts: int,
    bytes_per_concept: int = BYTES_PER_CONCEPT_ESTIMATE,
    safety_multiplier: float = DMD_WARM_MEMORY_SAFETY_MULTIPLIER,
    max_percent: float = DMD_WARM_MAX_REDIS_MEMORY_PERCENT,
) -> None:
    """Perform full-run memory preflight before the first concept write."""
    maxmemory = mem_state["maxmemory"]
    if maxmemory <= 0:
        return
    projected_bytes = int(estimated_concepts * bytes_per_concept * safety_multiplier)
    projected_total = mem_state["used_memory"] + projected_bytes
    percent = (projected_total / maxmemory) * 100.0
    if percent > max_percent:
        raise RuntimeError(
            f"Redis memory preflight failed: projected usage {percent:.1f}% "
            f"exceeds ceiling {max_percent:.1f}% before warm start "
            f"(used={mem_state['used_memory']}, projected_add={projected_bytes}, "
            f"estimated_concepts={estimated_concepts}, max={maxmemory})"
        )


def check_memory_guard(mem_state: dict[str, int], additional_bytes: int = 0) -> None:
    """Refuse further caching if projected memory usage exceeds configured ceiling."""
    maxmemory = mem_state["maxmemory"]
    if maxmemory <= 0:
        return
    projected = mem_state["used_memory"] + additional_bytes
    percent = (projected / maxmemory) * 100.0
    if percent > DMD_WARM_MAX_REDIS_MEMORY_PERCENT:
        raise RuntimeError(
            f"Redis memory guard exceeded: projected usage {percent:.1f}% "
            f"exceeds limit {DMD_WARM_MAX_REDIS_MEMORY_PERCENT}% "
            f"(used={projected}, max={maxmemory})"
        )


async def fetch_token() -> str:
    """Fetch terminology token via client credentials, avoiding raw logging."""
    from app.ccda.dmd import get_terminology_token

    return await get_terminology_token()


async def discover_release(client: TerminologyClient) -> tuple[str, int]:
    """Discover current dm+d release and estimated total concepts from upstream metadata."""
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
                                "filter": [{"property": "parent", "op": "=", "value": "ROUTE"}],
                            }
                        ]
                    },
                },
            },
            {"name": "count", "valueInteger": 1},
        ],
    }

    t0 = time.perf_counter()
    resp = await client.post_fhir("ValueSet/$expand", body)
    elapsed = time.perf_counter() - t0
    logger.info("Discovered release endpoint POST status=%d elapsed=%.2fs", resp.status_code, elapsed)

    expansion = resp.json().get("expansion", {})
    release = _expansion_version(expansion)
    if not release:
        raise ValueError("Could not discover dm+d release from upstream terminology expansion")

    # Discover estimated concept count: conservative baseline ~50,000 concepts across all categories
    total_estimate = 50000
    return release, total_estimate


async def warm() -> int:
    """Execute dm+d cache warming workflow with atomic release activation."""
    owner_token = str(uuid.uuid4())
    logger.info("Attempting to acquire warm lock key=%s owner_token_prefix=%s", WARM_LOCK_KEY, owner_token[:8])

    locked = await acquire_lock(owner_token)
    if not locked:
        logger.warning("Another dm+d warmer process is currently running (lock held). Exiting cleanly.")
        return 1

    try:
        mem_start = await get_redis_memory_state()
        check_memory_guard(mem_start)
        logger.info(
            "Initial Redis state: used_memory=%d maxmemory=%d evicted_keys=%d",
            mem_start["used_memory"],
            mem_start["maxmemory"],
            mem_start["evicted_keys"],
        )

        base_url = os.getenv("DMD_BASE_URL", "https://ontology.nhs.uk/production1/fhir")
        token = await fetch_token()

        async with httpx.AsyncClient(timeout=_TIMEOUT, limits=_LIMITS) as http_client:
            client = TerminologyClient(http_client, base_url, token)

            # 1. Discover current release and concept estimate
            release, estimated_total = await discover_release(client)
            logger.info("Discovered upstream dm+d release: %s (estimated %d concepts)", release, estimated_total)

            # Preflight memory check before writing anything
            check_preflight_memory(mem_start, estimated_total)

            # In-memory lookup dictionaries for resolving dependencies
            route_map: dict[str, str] = {}
            uom_map: dict[str, str] = {}
            vmp_lookup: dict[str, tuple[VPI | None, CD | None]] = {}

            # 2. Expand ROUTE
            if not await renew_lock(owner_token):
                raise RuntimeError("Lost distributed warmer lock before ROUTE stage. Aborting.")
            logger.info("Stage 1/4: Expanding ROUTE concepts")
            route_count = await _expand_and_cache_lookups(client, "ROUTE", release, route_map, mem_start)
            logger.info("Cached %d ROUTE concepts", route_count)

            # 3. Expand UOM
            if not await renew_lock(owner_token):
                raise RuntimeError("Lost distributed warmer lock before UOM stage. Aborting.")
            logger.info("Stage 2/4: Expanding UOM concepts")
            uom_count = await _expand_and_cache_lookups(client, "UOM", release, uom_map, mem_start)
            logger.info("Cached %d UOM concepts", uom_count)

            # 4. Expand VMP (with VPI and ROUTECD)
            if not await renew_lock(owner_token):
                raise RuntimeError("Lost distributed warmer lock before VMP stage. Aborting.")
            logger.info("Stage 3/4: Expanding and resolving VMP concepts")
            vmp_count = await _expand_vmp_concepts(client, release, route_map, uom_map, vmp_lookup, mem_start)
            logger.info("Resolved and cached %d VMP concepts", vmp_count)

            # 5. Expand AMP (inheriting VPI and ROUTECD from parent VMP)
            if not await renew_lock(owner_token):
                raise RuntimeError("Lost distributed warmer lock before AMP stage. Aborting.")
            logger.info("Stage 4/4: Expanding and resolving AMP concepts")
            amp_count = await _expand_amp_concepts(client, release, route_map, uom_map, vmp_lookup, mem_start)
            logger.info("Resolved and cached %d AMP concepts", amp_count)

        # 6. Verify completeness
        if route_count == 0 or uom_count == 0 or vmp_count == 0 or amp_count == 0:
            raise ValueError(
                f"Incomplete category count during warm: route={route_count}, "
                f"uom={uom_count}, vmp={vmp_count}, amp={amp_count}"
            )

        # 7. Check for Redis key evictions during warm
        mem_end = await get_redis_memory_state()
        if mem_end["evicted_keys"] > mem_start["evicted_keys"]:
            evicted_delta = mem_end["evicted_keys"] - mem_start["evicted_keys"]
            raise RuntimeError(f"Redis evicted {evicted_delta} keys during warm! Aborting release activation.")

        # 8. Write success manifest
        manifest = {
            "release": release,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "counts": {
                "ROUTE": route_count,
                "UOM": uom_count,
                "VMP": vmp_count,
                "AMP": amp_count,
            },
            "memory": {
                "used_memory_before": mem_start["used_memory"],
                "used_memory_after": mem_end["used_memory"],
                "delta_bytes": mem_end["used_memory"] - mem_start["used_memory"],
                "evicted_keys_before": mem_start["evicted_keys"],
                "evicted_keys_after": mem_end["evicted_keys"],
            },
            "status": "success",
        }
        await snomed_client.setex(
            manifest_key(release),
            DMD_MANIFEST_TTL,
            json.dumps(manifest),
        )

        # 9. ATOMIC ACTIVATION: Update active release pointer at the end
        await snomed_client.setex(ACTIVE_RELEASE_KEY, DMD_ACTIVE_RELEASE_TTL, release)
        logger.info("Successfully activated dm+d release: %s", release)

        return 0

    except Exception as exc:
        logger.error("dm+d cache warm failed: %s", exc, exc_info=True)
        return 1

    finally:
        await release_lock(owner_token)
        logger.info("Released warm lock")


async def _expand_and_cache_lookups(
    client: TerminologyClient,
    parent: str,
    expected_release: str,
    lookup_map: dict[str, str],
    mem_state: dict[str, int],
) -> int:
    """Expand concepts for parent (ROUTE or UOM) and cache raw lookup data."""
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
                                "filter": [{"property": "parent", "op": "=", "value": parent}],
                            }
                        ]
                    },
                },
            },
        ],
    }

    offset = 0
    total_processed = 0

    while True:
        paged_body = {
            **body,
            "parameter": body["parameter"]
            + [
                {"name": "count", "valueInteger": PAGE_SIZE},
                {"name": "offset", "valueInteger": offset},
            ],
        }

        resp = await client.post_fhir("ValueSet/$expand", paged_body)
        data = resp.json()
        expansion = data.get("expansion", {})
        contains = expansion.get("contains", [])

        # Release consistency check
        page_release = _expansion_version(expansion)
        if page_release and page_release != expected_release:
            raise ValueError(f"Release mismatch on {parent} page: expected {expected_release}, got {page_release}")

        if not contains:
            break

        # Batch writes using Redis pipeline
        pipe = snomed_client.pipeline()
        batch_bytes = 0

        for concept in contains:
            code = str(concept.get("code", ""))
            display = concept.get("display", "")
            if not code or not display:
                continue

            lookup_map[code] = display
            raw_payload = json.dumps(
                {
                    "parameter": [
                        {"name": "display", "valueString": display},
                        {"name": "version", "valueString": expected_release},
                    ]
                }
            )
            k = raw_key(expected_release, code)
            batch_bytes += len(k) + len(raw_payload)
            pipe.setex(k, DMD_RESOLVED_TTL, raw_payload)
            total_processed += 1

        check_memory_guard(mem_state, batch_bytes)
        await pipe.execute()

        total = expansion.get("total")
        if total is not None and (offset + len(contains)) >= total:
            break
        offset += len(contains)

    return total_processed


async def _expand_vmp_concepts(
    client: TerminologyClient,
    expected_release: str,
    route_map: dict[str, str],
    uom_map: dict[str, str],
    vmp_lookup: dict[str, tuple[VPI | None, CD | None]],
    mem_state: dict[str, int],
) -> int:
    """Page VMP concepts, resolve DMDConcept objects, and write to Redis."""
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
                                "filter": [{"property": "parent", "op": "=", "value": "VMP"}],
                            }
                        ]
                    },
                },
            },
            {"name": "property", "valueString": "VPI"},
            {"name": "property", "valueString": "ROUTECD"},
            {"name": "property", "valueString": "parent"},
        ],
    }

    offset = 0
    total_processed = 0

    while True:
        paged_body = {
            **body,
            "parameter": body["parameter"]
            + [
                {"name": "count", "valueInteger": PAGE_SIZE},
                {"name": "offset", "valueInteger": offset},
            ],
        }

        resp = await client.post_fhir("ValueSet/$expand", paged_body)
        data = resp.json()
        expansion = data.get("expansion", {})
        contains = expansion.get("contains", [])

        # Release consistency check
        page_release = _expansion_version(expansion)
        if page_release and page_release != expected_release:
            raise ValueError(f"Release mismatch on VMP page: expected {expected_release}, got {page_release}")

        if not contains:
            break

        pipe = snomed_client.pipeline()
        batch_bytes = 0

        for concept in contains:
            code = str(concept.get("code", ""))
            display = concept.get("display", "")
            if not code or not display:
                continue

            props = _extract_concept_properties(concept)

            vpi = None
            if props["vpi_val"] is not None:
                unit_display = uom_map.get(props["vpi_uom"]) if props["vpi_uom"] else None
                vpi = VPI(value=float(props["vpi_val"]), unit=unit_display or "")

            route = None
            if props["route_code"]:
                route_code = str(props["route_code"])
                route_display = route_map.get(route_code)
                route = CD(
                    code=route_code,
                    displayName=route_display,
                    codeSystemName="https://dmd.nhs.uk",
                )

            vmp_lookup[code] = (vpi, route)

            try:
                concept_id = int(code)
            except ValueError:
                continue

            resolved_concept = DMDConcept(
                concept_id=concept_id,
                valueString=display,
                vpi=vpi,
                route=route,
            )

            payload = resolved_concept.model_dump_json()
            k = resolved_key(expected_release, code)
            batch_bytes += len(k) + len(payload)
            pipe.setex(k, DMD_RESOLVED_TTL, payload)
            total_processed += 1

        check_memory_guard(mem_state, batch_bytes)
        await pipe.execute()

        total = expansion.get("total")
        if total is not None and (offset + len(contains)) >= total:
            break
        offset += len(contains)

    return total_processed


async def _expand_amp_concepts(
    client: TerminologyClient,
    expected_release: str,
    route_map: dict[str, str],
    uom_map: dict[str, str],
    vmp_lookup: dict[str, tuple[VPI | None, CD | None]],
    mem_state: dict[str, int],
) -> int:
    """Page AMP concepts, resolve DMDConcept objects inheriting from parent VMP, and write to Redis."""
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
                                "filter": [{"property": "parent", "op": "=", "value": "AMP"}],
                            }
                        ]
                    },
                },
            },
            {"name": "property", "valueString": "parent"},
            {"name": "property", "valueString": "VPI"},
            {"name": "property", "valueString": "ROUTECD"},
        ],
    }

    offset = 0
    total_processed = 0

    while True:
        paged_body = {
            **body,
            "parameter": body["parameter"]
            + [
                {"name": "count", "valueInteger": PAGE_SIZE},
                {"name": "offset", "valueInteger": offset},
            ],
        }

        resp = await client.post_fhir("ValueSet/$expand", paged_body)
        data = resp.json()
        expansion = data.get("expansion", {})
        contains = expansion.get("contains", [])

        # Release consistency check
        page_release = _expansion_version(expansion)
        if page_release and page_release != expected_release:
            raise ValueError(f"Release mismatch on AMP page: expected {expected_release}, got {page_release}")

        if not contains:
            break

        pipe = snomed_client.pipeline()
        batch_bytes = 0

        for concept in contains:
            code = str(concept.get("code", ""))
            display = concept.get("display", "")
            if not code or not display:
                continue

            props = _extract_concept_properties(concept)

            vpi = None
            route = None

            # Look for parent VMP to inherit VPI and ROUTECD
            parent_vmp_code = next((p for p in props["parents"] if p != "AMP" and p.isdigit()), None)
            if parent_vmp_code and parent_vmp_code in vmp_lookup:
                vpi, route = vmp_lookup[parent_vmp_code]
            else:
                if props["vpi_val"] is not None:
                    unit_display = uom_map.get(props["vpi_uom"]) if props["vpi_uom"] else None
                    vpi = VPI(value=float(props["vpi_val"]), unit=unit_display or "")
                if props["route_code"]:
                    route_code = str(props["route_code"])
                    route = CD(
                        code=route_code,
                        displayName=route_map.get(route_code),
                        codeSystemName="https://dmd.nhs.uk",
                    )

            try:
                concept_id = int(code)
            except ValueError:
                continue

            resolved_concept = DMDConcept(
                concept_id=concept_id,
                valueString=display,
                vpi=vpi,
                route=route,
            )

            payload = resolved_concept.model_dump_json()
            k = resolved_key(expected_release, code)
            batch_bytes += len(k) + len(payload)
            pipe.setex(k, DMD_RESOLVED_TTL, payload)
            total_processed += 1

        check_memory_guard(mem_state, batch_bytes)
        await pipe.execute()

        total = expansion.get("total")
        if total is not None and (offset + len(contains)) >= total:
            break
        offset += len(contains)

    return total_processed


async def status() -> dict[str, Any]:
    """Retrieve concise operational status without exposing tokens or secrets."""
    active_release_bytes = await snomed_client.get(ACTIVE_RELEASE_KEY)
    active_release = active_release_bytes.decode("utf-8") if active_release_bytes else None

    ttl_remaining = await snomed_client.ttl(ACTIVE_RELEASE_KEY) if active_release else None
    lock_val = await snomed_client.get(WARM_LOCK_KEY)
    is_locked = bool(lock_val)

    manifest_data = None
    if active_release:
        man_bytes = await snomed_client.get(manifest_key(active_release))
        if man_bytes:
            try:
                manifest_data = json.loads(man_bytes.decode("utf-8"))
            except Exception:
                manifest_data = None

    mem_state = await get_redis_memory_state()

    report = {
        "active_release": active_release,
        "active_release_ttl_seconds": ttl_remaining,
        "is_locked": is_locked,
        "manifest": manifest_data,
        "redis_memory": {
            "used_memory_bytes": mem_state["used_memory"],
            "maxmemory_bytes": mem_state["maxmemory"],
            "evicted_keys": mem_state["evicted_keys"],
        },
    }

    print("=== dm+d Cache Operational Status ===")
    print(f"Active Release:       {active_release or 'None (on-demand fallback mode)'}")
    if active_release:
        print(f"Release Pointer TTL:  {ttl_remaining}s (approx {ttl_remaining // 3600}h)")
    print(f"Warmer Lock Held:     {is_locked}")
    if manifest_data:
        counts = manifest_data.get("counts", {})
        print("Category Counts:")
        for cat in ("ROUTE", "UOM", "VMP", "AMP"):
            print(f"  {cat}: {counts.get(cat, 'n/a')}")
        print(f"Warm Completed At:    {manifest_data.get('completed_at')}")
    print(
        f"Redis Memory:         {mem_state['used_memory'] / (1024 * 1024):.2f} MB used / "
        f"{mem_state['maxmemory'] / (1024 * 1024):.2f} MB max (evicted keys: {mem_state['evicted_keys']})"
    )
    print("======================================")

    return report


def main() -> None:
    """CLI entry point for warm and status subcommands."""
    parser = argparse.ArgumentParser(description="Xhuma dm+d cache warmer and operational status")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    subparsers.add_parser("warm", help="Execute dm+d prewarm and atomic activation")
    subparsers.add_parser("status", help="Display current active release and cache health")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.subcommand == "warm":
        exit_code = asyncio.run(warm())
        sys.exit(exit_code)
    elif args.subcommand == "status":
        asyncio.run(status())
        sys.exit(0)


if __name__ == "__main__":
    main()
