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
import math
import os
import random
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from app.ccda.dmd import canonicalize_properties
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
    """HTTP client wrapper providing connection pooling, bounded monotonic retry deadline, jitter, and 401 refresh."""

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

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self.client.aclose()

    async def post_fhir(self, path: str, body: dict) -> httpx.Response:
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        deadline = time.monotonic() + self.retry_budget_s
        attempt = 0

        while True:
            attempt += 1
            now = time.monotonic()
            remaining = deadline - now
            if remaining <= 0:
                raise TimeoutError(
                    f"Overall retry budget of {self.retry_budget_s}s exhausted before attempt {attempt} for {url}"
                )

            # Clamp connect, read, write, and pool timeouts to remaining deadline
            attempt_timeout = httpx.Timeout(
                connect=min(_TIMEOUT.connect if _TIMEOUT.connect is not None else 15.0, remaining),
                read=min(_TIMEOUT.read if _TIMEOUT.read is not None else 300.0, remaining),
                write=min(_TIMEOUT.write if _TIMEOUT.write is not None else 30.0, remaining),
                pool=min(_TIMEOUT.pool if _TIMEOUT.pool is not None else 15.0, remaining),
            )

            headers = {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/fhir+json",
            }
            try:
                resp = await self.client.post(url, json=body, headers=headers, timeout=attempt_timeout)

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
                    now = time.monotonic()
                    remaining = deadline - now
                    if attempt >= self.max_attempts or remaining <= 0:
                        logger.error(
                            "Retry budget exhausted for %s (status %d, attempt %d/%d, remaining %.1fs)",
                            url,
                            resp.status_code,
                            attempt,
                            self.max_attempts,
                            remaining,
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

                    if sleep_time >= remaining:
                        logger.error(
                            "Retry sleep time (%.2fs) exceeds remaining deadline (%.2fs) for %s. Aborting.",
                            sleep_time,
                            remaining,
                            url,
                        )
                        resp.raise_for_status()

                    logger.warning(
                        "Transient HTTP %d from %s. Retrying in %.2fs (attempt %d/%d, remaining %.1fs)",
                        resp.status_code,
                        url,
                        sleep_time,
                        attempt,
                        self.max_attempts,
                        remaining,
                    )
                    await asyncio.sleep(sleep_time)
                    continue

                # Ordinary non-transient 4xx (400, 403, 404, 422) raises immediately without retry
                resp.raise_for_status()
                return resp

            except (httpx.NetworkError, httpx.TimeoutException) as exc:
                now = time.monotonic()
                remaining = deadline - now
                if attempt >= self.max_attempts or remaining <= 0:
                    logger.error(
                        "Network retry budget exhausted for %s (attempt %d/%d, remaining %.1fs): %s",
                        url,
                        attempt,
                        self.max_attempts,
                        remaining,
                        exc,
                    )
                    raise

                backoff = min(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)), BACKOFF_MAX_SECONDS)
                sleep_time = backoff + random.uniform(0.1, 0.5)

                if sleep_time >= remaining:
                    logger.error(
                        "Backoff (%.2fs) exceeds remaining deadline (%.2fs) after %s on %s. Aborting.",
                        sleep_time,
                        remaining,
                        type(exc).__name__,
                        url,
                    )
                    raise

                logger.warning(
                    "Network error %s from %s. Retrying in %.2fs (attempt %d/%d, remaining %.1fs)",
                    type(exc).__name__,
                    url,
                    sleep_time,
                    attempt,
                    self.max_attempts,
                    remaining,
                )
                await asyncio.sleep(sleep_time)


def resolved_key(release: str, concept_id: int | str) -> str:
    """Return key for prewarmed resolved DMDConcept."""
    return f"{RESOLVED_KEY_PREFIX}:{release}:{concept_id}"


def raw_key(release: str, concept_id: int | str, properties: list | None = None) -> str:
    """Return key for release-pinned raw concept lookup with canonical property signature."""
    prop_sig = canonicalize_properties(properties)
    return f"{RAW_KEY_PREFIX}:{release}:{concept_id}:properties:{prop_sig}"


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


async def fetch_token_in_memory() -> str:
    """Fetch terminology token via client credentials purely in memory without Redis caching."""
    client_id = os.getenv("DMD_CLIENT_ID")
    client_secret = os.getenv("DMD_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise ValueError("DMD_CLIENT_ID and DMD_CLIENT_SECRET must be set in environment variables.")

    url = os.getenv(
        "DMD_AUTH_URL",
        "https://ontology.nhs.uk/authorisation/auth/realms/nhs-digital-terminology/protocol/openid-connect/token",
    )
    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(url, data=data, headers=headers)
        resp.raise_for_status()
        token_data = resp.json()
        return token_data["access_token"]


async def fetch_token() -> str:
    """Fetch terminology token without writing to Redis."""
    return await fetch_token_in_memory()


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

    warm_start_time = time.monotonic()
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

        # 10. STRUCTURED COMPLETION EVENT (Heartbeat / Operational telemetry)
        total_concepts = route_count + uom_count + vmp_count + amp_count
        duration_seconds = round(time.monotonic() - warm_start_time, 2)
        completion_event = {
            "event": "warm_succeeded",
            "release": release,
            "duration_seconds": duration_seconds,
            "concept_count": total_concepts,
            "memory_delta_bytes": mem_end["used_memory"] - mem_start["used_memory"],
            "evictions": mem_end["evicted_keys"] - mem_start["evicted_keys"],
        }
        logger.info("dmd_cache_event %s", json.dumps(completion_event))

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


def _calculate_stats(values: list[int | float]) -> dict[str, float]:
    """Calculate mean, p50, p95, and max for a numeric list."""
    if not values:
        return {"mean": 0.0, "p50": 0.0, "p95": 0.0, "max": 0.0}
    s = sorted(values)
    mean_val = sum(s) / len(s)
    p50_val = s[int(len(s) * 0.50)]
    p95_val = s[min(int(len(s) * 0.95), len(s) - 1)]
    max_val = s[-1]
    return {
        "mean": round(float(mean_val), 1),
        "p50": round(float(p50_val), 1),
        "p95": round(float(p95_val), 1),
        "max": round(float(max_val), 1),
    }


def format_plan_summary(plan_data: dict[str, Any]) -> str:
    """Format human-readable dry-run / capacity planning summary."""
    lines = [
        "=== dm+d Cache Capacity Planning / Dry-Run Report ===",
        f"Target Release:               {plan_data['release']}",
        f"Unique Concept Count:         {plan_data['unique_concept_count']:,}",
        f"  ROUTE:                      {plan_data['counts_by_category'].get('ROUTE', 0):,}",
        f"  UOM:                        {plan_data['counts_by_category'].get('UOM', 0):,}",
        f"  VMP:                        {plan_data['counts_by_category'].get('VMP', 0):,}",
        f"  AMP:                        {plan_data['counts_by_category'].get('AMP', 0):,}",
        f"Projected HTTP Requests:      {plan_data['projected_terminology_requests']}",
        "Payload Sizes (p95):",
        f"  Raw FHIR Parameters:        {plan_data['sampled_raw_fhir_parameters']['p95_bytes']} bytes",
        f"  Resolved DMDConcept:        {plan_data['sampled_resolved_dmd_concept']['p95_bytes']} bytes",
        "Projected Memory Delta:",
        f"  Raw Cache:                  {plan_data['projected_raw_bytes'] / (1024 * 1024):.2f} MB",
        f"  Resolved Cache:             {plan_data['projected_resolved_bytes'] / (1024 * 1024):.2f} MB",
        f"  Key & Redis Metadata:       {plan_data['projected_redis_key_metadata_overhead_bytes'] / (1024 * 1024):.2f} MB",
        f"  Safety Multiplier:          {plan_data['safety_multiplier']}x",
        f"  Total Projected Delta:      {plan_data['projected_total_redis_delta_bytes'] / (1024 * 1024):.2f} MB",
        "Redis Memory Capacity:",
        f"  Current Used Memory:        {plan_data['current_redis_used_memory_bytes'] / (1024 * 1024):.2f} MB",
        f"  Redis Maxmemory:            {plan_data['redis_maxmemory_bytes'] / (1024 * 1024):.2f} MB",
        f"  Projected Total Memory:     {plan_data['projected_total_memory_bytes'] / (1024 * 1024):.2f} MB ({plan_data['projected_memory_percentage']}%)",
        "Runtime Duration:",
        f"  p95 Request Latency:        {plan_data['sampled_request_latency_seconds']['p95']}s",
        f"  Projected Duration:         {plan_data['projected_duration_seconds']}s",
        f"  Container Apps Timeout:     {plan_data['container_apps_timeout_seconds']}s",
        f"  Fits Replica Timeout:       {'YES' if plan_data['fits_timeout'] else 'NO - EXCEEDS TIMEOUT'}",
        f"Plan Result:                  {'FAIL-CLOSED / BLOCKED' if plan_data['fail_closed'] else 'CLEARED TO WARM'}",
    ]
    if plan_data.get("fail_reasons"):
        lines.append("Fail-Closed Reasons:")
        for r in plan_data["fail_reasons"]:
            lines.append(f"  - {r}")
    if plan_data.get("warnings"):
        lines.append("Warnings:")
        for w in plan_data["warnings"]:
            lines.append(f"  - {w}")
    lines.append("======================================================")
    return "\n".join(lines)


async def plan(
    sample_size: int = 10,
    safety_multiplier: float = DMD_WARM_MEMORY_SAFETY_MULTIPLIER,
    container_app_timeout_s: int = 1800,
    concurrency: int = 5,
    client: TerminologyClient | None = None,
    mem_state: dict[str, int] | None = None,
    category_counts: dict[str, int] | None = None,
    raw_samples: list[dict] | None = None,
    resolved_samples: list[DMDConcept] | None = None,
    request_latencies: list[float] | None = None,
    release: str | None = None,
    json_output: bool = False,
) -> dict[str, Any]:
    """Dry-run capacity planning mode. Strictly read-only: does not acquire lock, mutate Redis, or write keys."""
    if mem_state is None:
        mem_state = await get_redis_memory_state()

    current_used = mem_state.get("used_memory", 0)
    maxmemory = mem_state.get("maxmemory", 0)
    if maxmemory <= 0:
        maxmemory = 256 * 1024 * 1024

    discovered_release = release or "unknown"
    cat_counts = category_counts.copy() if category_counts else {}

    raw_sizes: list[int] = []
    resolved_sizes: list[int] = []
    latencies: list[float] = list(request_latencies) if request_latencies else []

    if raw_samples:
        for s in raw_samples:
            raw_sizes.append(len(json.dumps(s).encode("utf-8")))

    if resolved_samples:
        for r in resolved_samples:
            resolved_sizes.append(len(json.dumps(r.dict()).encode("utf-8")))

    if client is not None and not cat_counts:
        try:
            rel, _ = await discover_release(client)
            discovered_release = rel
            for cat in ("ROUTE", "UOM", "VMP", "AMP"):
                t0 = time.perf_counter()
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
                                            "filter": [{"property": "parent", "op": "=", "value": cat}],
                                        }
                                    ]
                                },
                            },
                        },
                        {"name": "count", "valueInteger": sample_size},
                    ],
                }
                resp = await client.post_fhir("ValueSet/$expand", body)
                elapsed = time.perf_counter() - t0
                latencies.append(elapsed)

                if resp.status_code == 200:
                    exp = resp.json().get("expansion", {})
                    cat_counts[cat] = exp.get("total", len(exp.get("contains", [])))
                    for concept in exp.get("contains", []):
                        raw_sizes.append(len(json.dumps(concept).encode("utf-8")))
                        if cat in ("VMP", "AMP"):
                            concept_id = concept.get("code")
                            if concept_id:
                                res_concept = DMDConcept(
                                    concept_id=int(concept_id) if str(concept_id).isdigit() else 0,
                                    valueString=concept.get("display") or "Sample Concept",
                                )
                                resolved_sizes.append(len(res_concept.model_dump_json().encode("utf-8")))
                else:
                    cat_counts[cat] = 0
        except Exception as exc:
            logger.warning("Could not sample terminology server in plan mode: %s", exc)

    if not cat_counts:
        cat_counts = {
            "ROUTE": 150,
            "UOM": 250,
            "VMP": 25000,
            "AMP": 35000,
        }
    if discovered_release == "unknown":
        discovered_release = "2026-10-15"

    if not raw_sizes:
        raw_sizes = [512, 768, 1024]
    if not resolved_sizes:
        resolved_sizes = [384, 512, 768]
    if not latencies:
        latencies = [0.15, 0.25, 0.40]

    raw_stats = _calculate_stats(raw_sizes)
    resolved_stats = _calculate_stats(resolved_sizes)
    latency_stats = _calculate_stats(latencies)

    unique_concepts = sum(cat_counts.values())

    vmp_pages = math.ceil(cat_counts.get("VMP", 0) / PAGE_SIZE) if cat_counts.get("VMP", 0) > 0 else 1
    amp_pages = math.ceil(cat_counts.get("AMP", 0) / PAGE_SIZE) if cat_counts.get("AMP", 0) > 0 else 1
    projected_http_requests = 1 + 1 + 1 + vmp_pages + amp_pages

    route_n = cat_counts.get("ROUTE", 0)
    uom_n = cat_counts.get("UOM", 0)
    vmp_n = cat_counts.get("VMP", 0)
    amp_n = cat_counts.get("AMP", 0)

    # ROUTE: 1 raw key per concept (properties:none)
    # UOM: 1 raw key per concept (properties:none)
    # VMP: 1 raw key (properties:none) + 1 raw key (properties:ROUTECD,VPI,parent) + 1 resolved key
    # AMP: 1 raw key (properties:none) + 1 raw key (properties:ROUTECD,VPI,parent) + 1 resolved key
    total_raw_keys = route_n + uom_n + (2 * vmp_n) + (2 * amp_n)
    total_resolved_keys = vmp_n + amp_n
    metadata_keys = 2
    total_keys = total_raw_keys + total_resolved_keys + metadata_keys

    raw_unit_bytes = raw_stats["p95"]
    resolved_unit_bytes = resolved_stats["p95"]

    projected_raw_bytes = int(total_raw_keys * raw_unit_bytes)
    projected_resolved_bytes = int(total_resolved_keys * resolved_unit_bytes)

    avg_key_name_bytes = 65
    redis_key_overhead = 128
    projected_overhead_bytes = int(total_keys * (avg_key_name_bytes + redis_key_overhead))

    allocator_frag = 1.25
    base_delta = projected_raw_bytes + projected_resolved_bytes + projected_overhead_bytes
    projected_total_redis_delta = int(base_delta * allocator_frag * safety_multiplier)

    projected_total_memory = current_used + projected_total_redis_delta
    projected_memory_percent = round((projected_total_memory / maxmemory) * 100.0, 2)

    lat_p95 = latency_stats["p95"]
    effective_concurrency = max(1, concurrency)
    projected_duration = round((projected_http_requests * lat_p95) / effective_concurrency, 2)
    fits_timeout = projected_duration <= container_app_timeout_s

    fail_closed = False
    fail_reasons: list[str] = []
    warnings: list[str] = []

    if projected_memory_percent > DMD_WARM_MAX_REDIS_MEMORY_PERCENT:
        fail_closed = True
        fail_reasons.append(
            f"Projected total Redis memory ({projected_memory_percent}%) exceeds guard limit "
            f"({DMD_WARM_MAX_REDIS_MEMORY_PERCENT}%). Maxmemory: {maxmemory} bytes."
        )

    if not fits_timeout:
        fail_closed = True
        fail_reasons.append(
            f"Projected warm duration ({projected_duration}s) exceeds Container Apps Job timeout "
            f"({container_app_timeout_s}s)."
        )

    if current_used >= maxmemory:
        fail_closed = True
        fail_reasons.append("Redis current used_memory is already at or above maxmemory.")

    if current_used > (maxmemory * 0.5):
        warnings.append(
            "Existing Redis memory exceeds 50% maxmemory before warm start (coexisting prior release or DB 0 keys)."
        )

    report = {
        "release": discovered_release,
        "counts_by_category": cat_counts,
        "unique_concept_count": unique_concepts,
        "projected_terminology_requests": projected_http_requests,
        "sampled_raw_fhir_parameters": {
            "mean_bytes": raw_stats["mean"],
            "p50_bytes": raw_stats["p50"],
            "p95_bytes": raw_stats["p95"],
            "max_bytes": raw_stats["max"],
        },
        "sampled_resolved_dmd_concept": {
            "mean_bytes": resolved_stats["mean"],
            "p50_bytes": resolved_stats["p50"],
            "p95_bytes": resolved_stats["p95"],
            "max_bytes": resolved_stats["max"],
        },
        "projected_raw_bytes": projected_raw_bytes,
        "projected_resolved_bytes": projected_resolved_bytes,
        "projected_redis_key_metadata_overhead_bytes": projected_overhead_bytes,
        "safety_multiplier": safety_multiplier,
        "allocator_fragmentation_multiplier": allocator_frag,
        "projected_total_redis_delta_bytes": projected_total_redis_delta,
        "current_redis_used_memory_bytes": current_used,
        "redis_maxmemory_bytes": maxmemory,
        "projected_total_memory_bytes": projected_total_memory,
        "projected_memory_percentage": projected_memory_percent,
        "sampled_request_latency_seconds": {
            "mean": latency_stats["mean"],
            "p50": latency_stats["p50"],
            "p95": latency_stats["p95"],
            "max": latency_stats["max"],
        },
        "projected_duration_seconds": projected_duration,
        "container_apps_timeout_seconds": container_app_timeout_s,
        "fits_timeout": fits_timeout,
        "fail_closed": fail_closed,
        "fail_reasons": fail_reasons,
        "warnings": warnings,
    }

    if json_output:
        print(json.dumps(report, indent=2))
    else:
        print(format_plan_summary(report))

    return report


def main() -> None:
    """CLI entry point for warm, plan, and status subcommands."""
    parser = argparse.ArgumentParser(description="Xhuma dm+d cache warmer and operational status")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    warm_parser = subparsers.add_parser("warm", help="Execute dm+d prewarm and atomic activation")
    warm_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Execute in read-only plan/dry-run mode without modifying Redis",
    )

    plan_parser = subparsers.add_parser("plan", help="Dry-run capacity planning without modifying Redis")
    plan_parser.add_argument(
        "--sample-size",
        type=int,
        default=10,
        help="Number of concepts to sample per category (default: 10)",
    )
    plan_parser.add_argument(
        "--json",
        action="store_true",
        help="Output machine-readable JSON only",
    )

    subparsers.add_parser("status", help="Display current active release and cache health")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.subcommand == "warm":
        if args.dry_run:
            result = asyncio.run(plan())
            sys.exit(1 if result.get("fail_closed") else 0)
        else:
            exit_code = asyncio.run(warm())
            sys.exit(exit_code)
    elif args.subcommand == "plan":
        result = asyncio.run(plan(sample_size=args.sample_size, json_output=args.json))
        sys.exit(1 if result.get("fail_closed") else 0)
    elif args.subcommand == "status":
        asyncio.run(status())
        sys.exit(0)


if __name__ == "__main__":
    main()
