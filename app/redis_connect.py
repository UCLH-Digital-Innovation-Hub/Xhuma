"""
Redis Connection Module
"""

import asyncio
import logging
import os
import ssl
from datetime import timedelta
from functools import wraps
from typing import Any

import redis.asyncio as redis
from redis.exceptions import ConnectionError, RedisError, TimeoutError

logger = logging.getLogger(__name__)

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_DB = int(os.getenv("REDIS_DB", 0))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD")
REDIS_SSL = os.getenv("REDIS_SSL", "false").lower() == "true"
REDIS_SSL_CERT_REQS = {
    "required": ssl.CERT_REQUIRED,
    "optional": ssl.CERT_OPTIONAL,
    "none": ssl.CERT_NONE,
}.get(os.getenv("REDIS_SSL_CERT_REQS", "required").lower(), ssl.CERT_REQUIRED)

POOL_MAX_CONNECTIONS = 10
POOL_TIMEOUT = 30
SOCKET_TIMEOUT = 30
SOCKET_CONNECT_TIMEOUT = 30
MAX_RETRIES = 3
RETRY_DELAY = 1


def retry_on_connection_error(max_retries: int = MAX_RETRIES, delay: int = RETRY_DELAY):
    """Retry Redis operations on connection errors."""

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_error = None

            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except (ConnectionError, TimeoutError) as exc:
                    last_error = exc
                    if attempt < max_retries - 1:
                        logger.warning(
                            "Retrying Redis operation, attempt %s/%s",
                            attempt + 2,
                            max_retries,
                        )
                        await asyncio.sleep(delay)

            logger.error(
                "Redis operation failed after %s attempts: %s",
                max_retries,
                last_error,
            )
            raise last_error

        return wrapper

    return decorator


class RedisClient:
    """Async Redis client; one pooled instance per database and application worker."""

    def __init__(self, db: int = REDIS_DB):
        """Initialize Redis client with connection pool."""
        kwargs = {
            "host": REDIS_HOST,
            "port": REDIS_PORT,
            "db": db,
            "max_connections": POOL_MAX_CONNECTIONS,
            "socket_timeout": SOCKET_TIMEOUT,
            "socket_connect_timeout": SOCKET_CONNECT_TIMEOUT,
            "retry_on_timeout": True,
            "decode_responses": False,  # Keep as bytes for MIME data
            "protocol": 2,  # Use RESP2 protocol for better compatibility
        }

        if REDIS_PASSWORD:
            kwargs["password"] = REDIS_PASSWORD

        if REDIS_SSL:
            kwargs["ssl"] = True
            kwargs["ssl_cert_reqs"] = REDIS_SSL_CERT_REQS
            if REDIS_SSL_CERT_REQS == ssl.CERT_NONE:
                logger.warning("REDIS_SSL_CERT_REQS is set to 'none'; SSL certificate validation is disabled")

        logger.warning(
            "Initializing Redis client connecting to %s:%s (SSL: %s)",
            REDIS_HOST,
            REDIS_PORT,
            REDIS_SSL,
        )
        self._client = redis.Redis(**kwargs)

    @retry_on_connection_error()
    async def ping(self) -> bool:
        """Test Redis connection."""
        return bool(await self._client.ping())

    @retry_on_connection_error()
    async def get(self, key: str) -> bytes | None:
        """Get value for key with automatic retry."""
        return await self._client.get(key)

    @retry_on_connection_error()
    async def setex(self, key: str, time: int | timedelta, value: str | bytes) -> bool:
        """Set key-value pair with expiry time."""
        return bool(await self._client.setex(key, time, value))

    @retry_on_connection_error()
    async def delete(self, *keys: str) -> int:
        """Delete one or more keys."""
        return int(await self._client.delete(*keys))

    @retry_on_connection_error()
    async def keys(self, pattern: str = "*") -> list:
        """Get keys matching pattern."""
        return await self._client.keys(pattern)

    @retry_on_connection_error()
    async def info(self) -> dict[str, Any]:
        """Get Redis server information."""
        return await self._client.info()

    @retry_on_connection_error()
    async def exists(self, key: str) -> bool:
        """Check if a key exists."""
        return bool(await self._client.exists(key))

    def pipeline(self):
        """Queue commands synchronously; use async with and await pipeline.execute()."""
        return self._client.pipeline()

    async def get_cache_info(self) -> dict:
        """Get cache statistics and memory usage."""
        try:
            info = await self.info()
            total_keys = await self._client.dbsize()
            memory_used = info.get("used_memory", 0)
            total_memory = info.get("maxmemory", 0)

            hits = info.get("keyspace_hits", 0)
            misses = info.get("keyspace_misses", 0)
            total_lookups = hits + misses

            stats = {
                "total_keys": total_keys,
                "memory_used": memory_used,
                "memory_limit": total_memory,
                "memory_usage_percent": ((memory_used / total_memory * 100) if total_memory else 0),
                "connected_clients": info.get("connected_clients", 0),
                "hit_rate": hits / total_lookups if total_lookups else 0,
            }

            if stats["memory_usage_percent"] > 80:
                logger.warning(
                    "Redis memory usage is high: %.1f%%",
                    stats["memory_usage_percent"],
                )

            return stats

        except RedisError as exc:
            logger.error("Failed to retrieve cache information: %s", exc)
            return {"error": str(exc)}

    async def close(self) -> None:
        """Close all connections in the pool."""
        await self._client.aclose(close_connection_pool=True)


redis_client = RedisClient()
redis_connect = redis_client

# Separate Redis database for SNOMED data
snomed_client = RedisClient(db=2)


async def get_cached_data(key: str) -> bytes | None:
    """Retrieve cached data for a given key."""
    try:
        return await redis_client.get(key)
    except RedisError as exc:
        logger.error("Error retrieving cached data: %s", exc)
        return None


async def cache_data(key: str, value: str | bytes, expiry: int = 3600) -> bool:
    """Cache data with expiry time."""
    try:
        return await redis_client.setex(key, expiry, value)
    except RedisError as exc:
        logger.error("Error caching data: %s", exc)
        return False


async def clear_cache(pattern: str = "*") -> bool:
    """Clear cache entries matching pattern."""
    try:
        keys = await redis_client.keys(pattern)
        if keys:
            return bool(await redis_client.delete(*keys))
        return True
    except RedisError as exc:
        logger.error("Error clearing cache: %s", exc)
        return False
