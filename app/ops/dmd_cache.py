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
from azure.identity import ManagedIdentityCredential
from azure.keyvault.secrets import SecretClient

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


def _resolve_expansion_concept(
    concept: dict,
    category: str,
    route_map: dict[str, str],
    uom_map: dict[str, str],
    vmp_lookup: dict[str, tuple[VPI | None, CD | None]],
    fallback_surrogate: bool = False,
) -> tuple[DMDConcept, VPI | None, CD | None]:
    """Pure transformation of an expansion concept into a DMDConcept instance.

    Returns (resolved_concept, vpi, route).
    Used consistently by warm() (with full lookup maps) and sample_payload_metrics()
    (with sampled maps / conservative surrogates).
    """
    code = str(concept.get("code", "")).strip()
    display = concept.get("display", "")
    if not code or not display:
        raise ValueError(f"Concept in {category} page has missing code or display: {concept}")

    try:
        concept_id = int(code)
    except ValueError:
        raise ValueError(f"{category} concept code is not an integer: {code}")

    props = _extract_concept_properties(concept)

    vpi = None
    route = None

    if category == "AMP":
        parent_vmp_code = next((p for p in props["parents"] if p != "AMP" and p.isdigit()), None)
        if parent_vmp_code and parent_vmp_code in vmp_lookup:
            vpi, route = vmp_lookup[parent_vmp_code]
        else:
            if props["vpi_val"] is not None:
                unit_disp = uom_map.get(props["vpi_uom"]) if props["vpi_uom"] else None
                if not unit_disp and fallback_surrogate and props["vpi_uom"]:
                    unit_disp = f"unit-{props['vpi_uom']}"
                vpi = VPI(value=float(props["vpi_val"]), unit=unit_disp or "")
            if props["route_code"]:
                route_code = str(props["route_code"])
                route_disp = route_map.get(route_code)
                if not route_disp and fallback_surrogate:
                    route_disp = f"route-{route_code}"
                route = CD(
                    code=route_code,
                    displayName=route_disp,
                    codeSystemName="https://dmd.nhs.uk",
                )
    else:  # VMP or others
        if props["vpi_val"] is not None:
            unit_disp = uom_map.get(props["vpi_uom"]) if props["vpi_uom"] else None
            if not unit_disp and fallback_surrogate and props["vpi_uom"]:
                unit_disp = f"unit-{props['vpi_uom']}"
            vpi = VPI(value=float(props["vpi_val"]), unit=unit_disp or "")
        if props["route_code"]:
            route_code = str(props["route_code"])
            route_disp = route_map.get(route_code)
            if not route_disp and fallback_surrogate:
                route_disp = f"route-{route_code}"
            route = CD(
                code=route_code,
                displayName=route_disp,
                codeSystemName="https://dmd.nhs.uk",
            )

    resolved_concept = DMDConcept(
        concept_id=concept_id,
        valueString=display,
        vpi=vpi,
        route=route,
    )
    return resolved_concept, vpi, route


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
    estimated_concepts: int = 0,
    bytes_per_concept: int = BYTES_PER_CONCEPT_ESTIMATE,
    safety_multiplier: float = DMD_WARM_MEMORY_SAFETY_MULTIPLIER,
    max_percent: float = DMD_WARM_MAX_REDIS_MEMORY_PERCENT,
    projected_bytes: int | None = None,
) -> None:
    """Perform full-run memory preflight before the first concept write."""
    maxmemory = mem_state.get("maxmemory", 0)
    if maxmemory <= 0:
        raise RuntimeError(
            f"Redis maxmemory must be configured and positive (got {maxmemory}). "
            "Failing closed to protect Redis DB 0/1 from unconstrained memory usage."
        )
    if projected_bytes is None:
        projected_bytes = int(estimated_concepts * bytes_per_concept * safety_multiplier)
    projected_total = mem_state.get("used_memory", 0) + projected_bytes
    percent = (projected_total / maxmemory) * 100.0
    if percent > max_percent:
        raise RuntimeError(
            f"Redis memory preflight failed: projected usage {percent:.1f}% "
            f"exceeds ceiling {max_percent:.1f}% before warm start "
            f"(used={mem_state.get('used_memory', 0)}, projected_add={projected_bytes}, "
            f"estimated_concepts={estimated_concepts}, max={maxmemory})"
        )


def check_memory_guard(mem_state: dict[str, int], additional_bytes: int = 0) -> None:
    """Refuse further caching if projected memory usage exceeds configured ceiling."""
    maxmemory = mem_state.get("maxmemory", 0)
    if maxmemory <= 0:
        raise RuntimeError(
            f"Redis maxmemory must be configured and positive (got {maxmemory}). "
            "Failing closed to protect Redis DB 0/1 from unconstrained memory usage."
        )
    projected = mem_state.get("used_memory", 0) + additional_bytes
    percent = (projected / maxmemory) * 100.0
    if percent > DMD_WARM_MAX_REDIS_MEMORY_PERCENT:
        raise RuntimeError(
            f"Redis memory guard exceeded: projected usage {percent:.1f}% "
            f"exceeds limit {DMD_WARM_MAX_REDIS_MEMORY_PERCENT}% "
            f"(used={projected}, max={maxmemory})"
        )


def _load_dmd_credentials() -> tuple[str, str]:
    """Resolve dm+d client credentials with fail-closed precedence.

    Precedence:
    1. If BOTH DMD_CLIENT_ID and DMD_CLIENT_SECRET are set in environment, use them.
    2. If exactly ONE of DMD_CLIENT_ID or DMD_CLIENT_SECRET is set, fail closed (no mixing).
    3. If neither is set, retrieve 'dmd-client-id' and 'dmd-client-secret' from Azure Key Vault
       using ManagedIdentityCredential(client_id=AZURE_CLIENT_ID) and SecretClient(vault_url=KEY_VAULT_URL).
       Both KEY_VAULT_URL and AZURE_CLIENT_ID must be set.
    4. Validate both returned values are non-empty strings.
    5. Never log secret values, token values, client secrets, or Key Vault response bodies.
    """
    env_id = os.getenv("DMD_CLIENT_ID")
    env_secret = os.getenv("DMD_CLIENT_SECRET")

    has_id = bool(env_id and env_id.strip())
    has_secret = bool(env_secret and env_secret.strip())

    if has_id and has_secret:
        return env_id.strip(), env_secret.strip()

    if has_id or has_secret:
        raise ValueError(
            "Partial dm+d credentials configured in environment: both DMD_CLIENT_ID and DMD_CLIENT_SECRET "
            "must be provided together, or neither to use Key Vault."
        )

    vault_url = os.getenv("KEY_VAULT_URL")
    azure_client_id = os.getenv("AZURE_CLIENT_ID")

    if not vault_url or not vault_url.strip() or not azure_client_id or not azure_client_id.strip():
        raise ValueError(
            "dm+d credentials not found in environment and Key Vault configuration is incomplete: "
            "KEY_VAULT_URL and AZURE_CLIENT_ID must both be set when DMD_CLIENT_ID/DMD_CLIENT_SECRET are absent."
        )

    try:
        with ManagedIdentityCredential(client_id=azure_client_id.strip()) as credential:
            with SecretClient(vault_url=vault_url.strip(), credential=credential) as client:
                id_bundle = client.get_secret("dmd-client-id")
                secret_bundle = client.get_secret("dmd-client-secret")
                dmd_client_id = id_bundle.value if id_bundle else None
                dmd_client_secret = secret_bundle.value if secret_bundle else None
    except Exception as exc:
        logger.error("Failed to retrieve dm+d credentials from Key Vault.")
        raise RuntimeError("Failed to retrieve dm+d credentials from Key Vault using managed identity.") from exc

    if not dmd_client_id or not dmd_client_id.strip():
        raise ValueError("Key Vault secret 'dmd-client-id' is missing or empty.")
    if not dmd_client_secret or not dmd_client_secret.strip():
        raise ValueError("Key Vault secret 'dmd-client-secret' is missing or empty.")

    return dmd_client_id.strip(), dmd_client_secret.strip()


async def fetch_token_in_memory() -> str:
    """Fetch terminology token via client credentials purely in memory without Redis caching."""
    client_id, client_secret = _load_dmd_credentials()

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


async def discover_release_capacity(client: TerminologyClient) -> dict[str, Any]:
    """Discover current dm+d release and authoritative category totals from upstream terminology expansion."""
    categories = ("ROUTE", "UOM", "VMP", "AMP")
    counts: dict[str, int] = {}
    page_counts: dict[str, int] = {}
    discovered_release: str | None = None

    for cat in categories:
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
                {"name": "count", "valueInteger": 1},
            ],
        }
        resp = await client.post_fhir("ValueSet/$expand", body)
        resp.raise_for_status()
        expansion = resp.json().get("expansion", {})
        ver = _expansion_version(expansion)
        if not ver:
            raise ValueError(f"Could not discover dm+d release from upstream expansion for {cat}")
        if discovered_release is None:
            discovered_release = ver
        elif ver != discovered_release:
            raise ValueError(
                f"Release mismatch across categories during count discovery: "
                f"expected {discovered_release}, got {ver} for {cat}"
            )
        total = expansion.get("total")
        if total is None or not isinstance(total, int) or total <= 0:
            raise ValueError(f"Invalid or missing total count for category {cat}: {total}")
        counts[cat] = total
        page_counts[cat] = math.ceil(total / PAGE_SIZE)

    total_concepts = sum(counts.values())
    return {
        "release": discovered_release,
        "counts": counts,
        "total_concepts": total_concepts,
        "page_counts": page_counts,
    }


async def discover_release(client: TerminologyClient) -> tuple[str, int]:
    """Discover current dm+d release and estimated total concepts from upstream metadata."""
    cap = await discover_release_capacity(client)
    return cap["release"], cap["total_concepts"]


async def sample_payload_metrics(
    client: TerminologyClient,
    release: str,
    sample_size: int = 10,
) -> tuple[list[int], list[int], list[float]]:
    """Sample small batches of concepts from upstream to measure real payload sizes and latencies."""
    raw_sizes: list[int] = []
    resolved_sizes: list[int] = []
    latencies: list[float] = []

    sampled_route_map: dict[str, str] = {}
    sampled_uom_map: dict[str, str] = {}
    sampled_vmp_lookup: dict[str, tuple[VPI | None, CD | None]] = {}

    for cat in ("ROUTE", "UOM"):
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
        t0 = time.perf_counter()
        resp = await client.post_fhir("ValueSet/$expand", body)
        elapsed = time.perf_counter() - t0
        latencies.append(elapsed)
        resp.raise_for_status()
        expansion = resp.json().get("expansion", {})
        contains = expansion.get("contains", [])
        if not contains:
            raise ValueError(f"Terminology server returned empty concepts when sampling {cat}")
        for concept in contains:
            code = str(concept.get("code", "")).strip()
            display = concept.get("display", "")
            if code and display:
                if cat == "ROUTE":
                    sampled_route_map[code] = display
                else:
                    sampled_uom_map[code] = display
            raw_payload = json.dumps(
                {
                    "parameter": [
                        {"name": "display", "valueString": display},
                        {"name": "version", "valueString": release},
                    ]
                }
            )
            raw_sizes.append(len(raw_payload.encode("utf-8")))

    for cat in ("VMP", "AMP"):
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
                {"name": "property", "valueString": "parent"},
                {"name": "property", "valueString": "VPI"},
                {"name": "property", "valueString": "ROUTECD"},
                {"name": "count", "valueInteger": sample_size},
            ],
        }
        t0 = time.perf_counter()
        resp = await client.post_fhir("ValueSet/$expand", body)
        elapsed = time.perf_counter() - t0
        latencies.append(elapsed)
        resp.raise_for_status()
        expansion = resp.json().get("expansion", {})
        contains = expansion.get("contains", [])
        if not contains:
            raise ValueError(f"Terminology server returned empty concepts when sampling {cat}")
        for concept in contains:
            resolved_concept, vpi, route = _resolve_expansion_concept(
                concept=concept,
                category=cat,
                route_map=sampled_route_map,
                uom_map=sampled_uom_map,
                vmp_lookup=sampled_vmp_lookup,
                fallback_surrogate=True,
            )
            code = str(concept.get("code", "")).strip()
            if cat == "VMP" and code:
                sampled_vmp_lookup[code] = (vpi, route)

            resolved_sizes.append(len(resolved_concept.model_dump_json().encode("utf-8")))

    return raw_sizes, resolved_sizes, latencies


def build_capacity_plan(
    release: str,
    category_counts: dict[str, int],
    mem_state: dict[str, int],
    raw_payload_bytes: list[int],
    resolved_payload_bytes: list[int],
    latencies: list[float],
    sample_request_count: int = 8,
    safety_multiplier: float = DMD_WARM_MEMORY_SAFETY_MULTIPLIER,
    container_app_timeout_s: int = 1800,
) -> dict[str, Any]:
    """Unified capacity, memory, key count, and duration model for plan() and warm()."""
    route_n = category_counts.get("ROUTE", 0)
    uom_n = category_counts.get("UOM", 0)
    vmp_n = category_counts.get("VMP", 0)
    amp_n = category_counts.get("AMP", 0)
    unique_concepts = route_n + uom_n + vmp_n + amp_n

    # Real page counts across all categories
    route_pages = math.ceil(route_n / PAGE_SIZE) if route_n > 0 else 1
    uom_pages = math.ceil(uom_n / PAGE_SIZE) if uom_n > 0 else 1
    vmp_pages = math.ceil(vmp_n / PAGE_SIZE) if vmp_n > 0 else 1
    amp_pages = math.ceil(amp_n / PAGE_SIZE) if amp_n > 0 else 1
    total_expansion_pages = route_pages + uom_pages + vmp_pages + amp_pages
    projected_http_requests = total_expansion_pages + sample_request_count

    # Exact correspondence to actual warm() writes:
    # ROUTE: 1 raw key per concept
    # UOM: 1 raw key per concept
    # VMP: 1 resolved key per concept
    # AMP: 1 resolved key per concept
    # Manifest: 1 key
    # Active release: 1 key
    total_raw_keys = route_n + uom_n
    total_resolved_keys = vmp_n + amp_n
    metadata_keys = 2
    total_keys = total_raw_keys + total_resolved_keys + metadata_keys

    raw_stats = _calculate_stats(raw_payload_bytes)
    resolved_stats = _calculate_stats(resolved_payload_bytes)
    latency_stats = _calculate_stats(latencies)

    # Conservative capacity admission: use observed maximum sampled unit bytes
    raw_unit_bytes = raw_stats["max"]
    resolved_unit_bytes = resolved_stats["max"]

    projected_raw_bytes = int(total_raw_keys * raw_unit_bytes)
    projected_resolved_bytes = int(total_resolved_keys * resolved_unit_bytes)

    avg_key_name_bytes = 65
    redis_key_overhead = 128
    projected_overhead_bytes = int(total_keys * (avg_key_name_bytes + redis_key_overhead))

    allocator_frag = 1.25
    base_delta = projected_raw_bytes + projected_resolved_bytes + projected_overhead_bytes
    projected_total_redis_delta = int(base_delta * allocator_frag * safety_multiplier)

    current_used = mem_state.get("used_memory", 0)
    maxmemory = mem_state.get("maxmemory", 0)

    fail_closed = False
    fail_reasons: list[str] = []
    warnings: list[str] = []

    if maxmemory <= 0:
        fail_closed = True
        fail_reasons.append(
            f"Redis maxmemory must be configured and positive (got {maxmemory} bytes). "
            "Failing closed to protect Redis DB 0/1 from unconstrained memory usage."
        )
        projected_total_memory = current_used + projected_total_redis_delta
        projected_memory_percent = 0.0
    else:
        projected_total_memory = current_used + projected_total_redis_delta
        projected_memory_percent = round((projected_total_memory / maxmemory) * 100.0, 2)
        if projected_memory_percent > DMD_WARM_MAX_REDIS_MEMORY_PERCENT:
            fail_closed = True
            fail_reasons.append(
                f"Projected total Redis memory ({projected_memory_percent}%) exceeds guard limit "
                f"({DMD_WARM_MAX_REDIS_MEMORY_PERCENT}%). Maxmemory: {maxmemory} bytes."
            )
        if current_used >= maxmemory:
            fail_closed = True
            fail_reasons.append("Redis current used_memory is already at or above maxmemory.")
        if current_used > (maxmemory * 0.5):
            warnings.append(
                "Existing Redis memory exceeds 50% maxmemory before warm start (coexisting prior release or DB 0 keys)."
            )

    # Sequential concurrency = 1
    lat_p95 = latency_stats["p95"]
    projected_duration = round(projected_http_requests * lat_p95, 2)
    fits_timeout = projected_duration <= container_app_timeout_s
    if not fits_timeout:
        fail_closed = True
        fail_reasons.append(
            f"Projected warm duration ({projected_duration}s) exceeds Container Apps Job timeout "
            f"({container_app_timeout_s}s)."
        )

    return {
        "release": release,
        "counts_by_category": category_counts,
        "unique_concept_count": unique_concepts,
        "page_counts": {
            "ROUTE": route_pages,
            "UOM": uom_pages,
            "VMP": vmp_pages,
            "AMP": amp_pages,
        },
        "projected_terminology_requests": projected_http_requests,
        "key_counts_by_type": {
            "raw": total_raw_keys,
            "resolved": total_resolved_keys,
            "metadata": metadata_keys,
        },
        "total_keys": total_keys,
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
        baseline_evicted_keys = mem_start["evicted_keys"]
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

            # 1. Discover whole-release category totals and release version
            cap_discovery = await discover_release_capacity(client)
            release = cap_discovery["release"]
            counts = cap_discovery["counts"]
            expected_ROUTE = counts["ROUTE"]
            expected_UOM = counts["UOM"]
            expected_VMP = counts["VMP"]
            expected_AMP = counts["AMP"]
            logger.info("Discovered upstream dm+d release: %s (counts: %s)", release, counts)

            # 2. Sample payload metrics and latencies from discovered release
            raw_sizes, resolved_sizes, sample_latencies = await sample_payload_metrics(client, release, sample_size=10)

            # 3. Build unified capacity and duration plan
            plan_data = build_capacity_plan(
                release=release,
                category_counts=counts,
                mem_state=mem_start,
                raw_payload_bytes=raw_sizes,
                resolved_payload_bytes=resolved_sizes,
                latencies=sample_latencies,
                sample_request_count=8,
            )

            # Preflight safety checks before writing anything
            if plan_data.get("fail_closed"):
                reasons = "; ".join(plan_data.get("fail_reasons", []))
                raise RuntimeError(f"Warm preflight failed: {reasons}")

            # Double check preflight memory guard
            check_preflight_memory(
                mem_start,
                projected_bytes=plan_data["projected_total_redis_delta_bytes"],
            )

            # In-memory lookup dictionaries for resolving dependencies
            route_map: dict[str, str] = {}
            uom_map: dict[str, str] = {}
            vmp_lookup: dict[str, tuple[VPI | None, CD | None]] = {}

            # Stage 1/4: Expand ROUTE
            if not await renew_lock(owner_token):
                raise RuntimeError("Lost distributed warmer lock before ROUTE stage. Aborting.")
            logger.info("Stage 1/4: Expanding ROUTE concepts (expected: %d)", expected_ROUTE)
            route_count = await _expand_and_cache_lookups(
                client, "ROUTE", release, route_map, baseline_evicted_keys, expected_ROUTE
            )
            logger.info("Cached %d ROUTE concepts", route_count)

            # Stage 2/4: Expand UOM
            if not await renew_lock(owner_token):
                raise RuntimeError("Lost distributed warmer lock before UOM stage. Aborting.")
            logger.info("Stage 2/4: Expanding UOM concepts (expected: %d)", expected_UOM)
            uom_count = await _expand_and_cache_lookups(
                client, "UOM", release, uom_map, baseline_evicted_keys, expected_UOM
            )
            logger.info("Cached %d UOM concepts", uom_count)

            # Stage 3/4: Expand VMP (with VPI and ROUTECD)
            if not await renew_lock(owner_token):
                raise RuntimeError("Lost distributed warmer lock before VMP stage. Aborting.")
            logger.info("Stage 3/4: Expanding and resolving VMP concepts (expected: %d)", expected_VMP)
            vmp_count = await _expand_vmp_concepts(
                client, release, route_map, uom_map, vmp_lookup, baseline_evicted_keys, expected_VMP
            )
            logger.info("Resolved and cached %d VMP concepts", vmp_count)

            # Stage 4/4: Expand AMP (inheriting VPI and ROUTECD from parent VMP)
            if not await renew_lock(owner_token):
                raise RuntimeError("Lost distributed warmer lock before AMP stage. Aborting.")
            logger.info("Stage 4/4: Expanding and resolving AMP concepts (expected: %d)", expected_AMP)
            amp_count = await _expand_amp_concepts(
                client, release, route_map, uom_map, vmp_lookup, baseline_evicted_keys, expected_AMP
            )
            logger.info("Resolved and cached %d AMP concepts", amp_count)

        # 6. Verify exact completeness
        if (
            route_count != expected_ROUTE
            or uom_count != expected_UOM
            or vmp_count != expected_VMP
            or amp_count != expected_AMP
        ):
            raise ValueError(
                f"Incomplete category count during warm: route={route_count}/{expected_ROUTE}, "
                f"uom={uom_count}/{expected_UOM}, vmp={vmp_count}/{expected_VMP}, amp={amp_count}/{expected_AMP}"
            )

        # 7. Check for Redis key evictions during warm
        mem_end = await get_redis_memory_state()
        if mem_end["evicted_keys"] > baseline_evicted_keys:
            evicted_delta = mem_end["evicted_keys"] - baseline_evicted_keys
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
    baseline_evicted_keys: int,
    expected_count: int,
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
        resp.raise_for_status()
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
            code = str(concept.get("code", "")).strip()
            display = concept.get("display", "")
            if not code or not display:
                raise ValueError(f"Concept in {parent} page has missing code or display: {concept}")

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
            batch_bytes += len(k.encode("utf-8")) + len(raw_payload.encode("utf-8"))
            pipe.setex(k, DMD_RESOLVED_TTL, raw_payload)
            total_processed += 1

        # Per-batch guard: inspect CURRENT Redis state before writing
        current_mem = await get_redis_memory_state()
        if current_mem["evicted_keys"] > baseline_evicted_keys:
            evicted_delta = current_mem["evicted_keys"] - baseline_evicted_keys
            raise RuntimeError(
                f"Redis evicted {evicted_delta} keys during {parent} warm stage! "
                f"(current={current_mem['evicted_keys']}, baseline={baseline_evicted_keys}). Aborting."
            )
        check_memory_guard(current_mem, batch_bytes)
        await pipe.execute()

        total = expansion.get("total")
        if total is not None and (offset + len(contains)) >= total:
            break
        offset += len(contains)

    if total_processed != expected_count:
        raise ValueError(f"Processed count mismatch for {parent}: expected {expected_count}, got {total_processed}")

    return total_processed


async def _expand_vmp_concepts(
    client: TerminologyClient,
    expected_release: str,
    route_map: dict[str, str],
    uom_map: dict[str, str],
    vmp_lookup: dict[str, tuple[VPI | None, CD | None]],
    baseline_evicted_keys: int,
    expected_count: int,
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
        resp.raise_for_status()
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
            resolved_concept, vpi, route = _resolve_expansion_concept(
                concept=concept,
                category="VMP",
                route_map=route_map,
                uom_map=uom_map,
                vmp_lookup=vmp_lookup,
            )
            code = str(concept.get("code", "")).strip()
            vmp_lookup[code] = (vpi, route)

            payload = resolved_concept.model_dump_json()
            k = resolved_key(expected_release, code)
            batch_bytes += len(k.encode("utf-8")) + len(payload.encode("utf-8"))
            pipe.setex(k, DMD_RESOLVED_TTL, payload)
            total_processed += 1

        current_mem = await get_redis_memory_state()
        if current_mem["evicted_keys"] > baseline_evicted_keys:
            evicted_delta = current_mem["evicted_keys"] - baseline_evicted_keys
            raise RuntimeError(
                f"Redis evicted {evicted_delta} keys during VMP warm stage! "
                f"(current={current_mem['evicted_keys']}, baseline={baseline_evicted_keys}). Aborting."
            )
        check_memory_guard(current_mem, batch_bytes)
        await pipe.execute()

        total = expansion.get("total")
        if total is not None and (offset + len(contains)) >= total:
            break
        offset += len(contains)

    if total_processed != expected_count:
        raise ValueError(f"Processed count mismatch for VMP: expected {expected_count}, got {total_processed}")

    return total_processed


async def _expand_amp_concepts(
    client: TerminologyClient,
    expected_release: str,
    route_map: dict[str, str],
    uom_map: dict[str, str],
    vmp_lookup: dict[str, tuple[VPI | None, CD | None]],
    baseline_evicted_keys: int,
    expected_count: int,
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
        resp.raise_for_status()
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
            resolved_concept, _, _ = _resolve_expansion_concept(
                concept=concept,
                category="AMP",
                route_map=route_map,
                uom_map=uom_map,
                vmp_lookup=vmp_lookup,
            )
            code = str(concept.get("code", "")).strip()
            payload = resolved_concept.model_dump_json()
            k = resolved_key(expected_release, code)
            batch_bytes += len(k.encode("utf-8")) + len(payload.encode("utf-8"))
            pipe.setex(k, DMD_RESOLVED_TTL, payload)
            total_processed += 1

        current_mem = await get_redis_memory_state()
        if current_mem["evicted_keys"] > baseline_evicted_keys:
            evicted_delta = current_mem["evicted_keys"] - baseline_evicted_keys
            raise RuntimeError(
                f"Redis evicted {evicted_delta} keys during AMP warm stage! "
                f"(current={current_mem['evicted_keys']}, baseline={baseline_evicted_keys}). Aborting."
            )
        check_memory_guard(current_mem, batch_bytes)
        await pipe.execute()

        total = expansion.get("total")
        if total is not None and (offset + len(contains)) >= total:
            break
        offset += len(contains)

    if total_processed != expected_count:
        raise ValueError(f"Processed count mismatch for AMP: expected {expected_count}, got {total_processed}")

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
        "Cache Key Writes Modelled:",
        f"  Raw Lookup Keys:            {plan_data['key_counts_by_type']['raw']:,}",
        f"  Resolved DMDConcept Keys:   {plan_data['key_counts_by_type']['resolved']:,}",
        f"  Metadata Keys:              {plan_data['key_counts_by_type']['metadata']:,}",
        f"  Total Keys:                 {plan_data['total_keys']:,}",
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
        "Runtime Duration (Sequential Concurrency = 1):",
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
    concurrency: int = 1,
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
    if client is None and category_counts is None:
        raise ValueError(
            "A live TerminologyClient or explicit category_counts test argument must be provided. "
            "Synthetic production fallback is prohibited."
        )

    if mem_state is None:
        mem_state = await get_redis_memory_state()

    if client is None:
        discovered_release = release or "test-release"
        cat_counts = category_counts.copy()
        raw_sizes = (
            [len(json.dumps(s).encode("utf-8")) for s in raw_samples]
            if raw_samples
            else [
                len(
                    json.dumps(
                        {
                            "parameter": [
                                {"name": "display", "valueString": "Sample"},
                                {"name": "version", "valueString": discovered_release},
                            ]
                        }
                    ).encode("utf-8")
                )
            ]
        )
        resolved_sizes = (
            [len(r.model_dump_json().encode("utf-8")) for r in resolved_samples]
            if resolved_samples
            else [len(DMDConcept(concept_id=1, valueString="Sample").model_dump_json().encode("utf-8"))]
        )
        latencies = list(request_latencies) if request_latencies else [0.1]
        sample_request_count = len(latencies)
    else:
        # Live discovery & sampling path
        cap_discovery = await discover_release_capacity(client)
        discovered_release = cap_discovery["release"]
        cat_counts = cap_discovery["counts"]

        raw_sizes, resolved_sizes, latencies = await sample_payload_metrics(
            client=client,
            release=discovered_release,
            sample_size=sample_size,
        )
        sample_request_count = 8  # 4 discovery calls + 4 sampling calls

    plan_data = build_capacity_plan(
        release=discovered_release,
        category_counts=cat_counts,
        mem_state=mem_state,
        raw_payload_bytes=raw_sizes,
        resolved_payload_bytes=resolved_sizes,
        latencies=latencies,
        sample_request_count=sample_request_count,
        safety_multiplier=safety_multiplier,
        container_app_timeout_s=container_app_timeout_s,
    )

    if json_output:
        print(json.dumps(plan_data, indent=2))
    else:
        print(format_plan_summary(plan_data))

    return plan_data


async def plan_cli(sample_size: int = 10, json_output: bool = False) -> int:
    """Execute real read-only CLI plan/dry-run against live terminology and Redis INFO.

    Strictly read-only: does not acquire lock, mutate Redis, or write keys.
    """
    mem_state = await get_redis_memory_state()
    token = await fetch_token_in_memory()
    base_url = os.getenv("DMD_BASE_URL", "https://ontology.nhs.uk/production1/fhir")

    async with httpx.AsyncClient(timeout=_TIMEOUT, limits=_LIMITS) as http_client:
        client = TerminologyClient(http_client, base_url, token)
        plan_data = await plan(
            sample_size=sample_size,
            client=client,
            mem_state=mem_state,
            json_output=json_output,
        )
    return 1 if plan_data.get("fail_closed") else 0


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
            try:
                exit_code = asyncio.run(plan_cli(sample_size=10, json_output=False))
            except Exception as exc:
                logger.error("Warm dry-run failed: %s", exc)
                exit_code = 1
            sys.exit(exit_code)
        else:
            exit_code = asyncio.run(warm())
            sys.exit(exit_code)
    elif args.subcommand == "plan":
        try:
            exit_code = asyncio.run(plan_cli(sample_size=args.sample_size, json_output=args.json))
        except Exception as exc:
            logger.error("Plan failed: %s", exc)
            exit_code = 1
        sys.exit(exit_code)
    elif args.subcommand == "status":
        asyncio.run(status())
        sys.exit(0)


if __name__ == "__main__":
    main()
