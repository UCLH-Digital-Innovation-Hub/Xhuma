"""
Redis Connection Module
"""

import logging
import os
import time
from functools import wraps
from typing import Any, Dict, Optional, Union

import redis
from redis.connection import Connection, ConnectionPool, SSLConnection
from redis.exceptions import ConnectionError, RedisError, TimeoutError

logger = logging.getLogger(__name__)

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_DB = int(os.getenv("REDIS_DB", 0))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD")
REDIS_SSL = os.getenv("REDIS_SSL", "false").lower() == "true"
REDIS_SSL_CERT_REQS = os.getenv("REDIS_SSL_CERT_REQS", "required").lower()

POOL_MAX_CONNECTIONS = 10
SOCKET_TIMEOUT = 5
SOCKET_CONNECT_TIMEOUT = 5
MAX_RETRIES = 3
RETRY_DELAY = 1


def retry_on_connection_error(max_retries: int = MAX_RETRIES, delay: int = RETRY_DELAY):
    """Retry Redis operations on connection errors."""

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_error = None

            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except (ConnectionError, TimeoutError) as exc:
                    last_error = exc
                    if attempt < max_retries - 1:
                        logger.warning(
                            "Retrying Redis operation, attempt %s/%s",
                            attempt + 2,
                            max_retries,
                        )
                        time.sleep(delay)

            logger.error(
                "Redis operation failed after %s attempts: %s",
                max_retries,
                last_error,
            )
            raise last_error

        return wrapper

    return decorator


class RedisClient:
    """Redis client with connection pooling and error handling."""

    def __init__(self, db: int = REDIS_DB):
        """Initialize Redis client with connection pool."""
        pool_kwargs = {
            "host": REDIS_HOST,
            "port": REDIS_PORT,
            "db": db,
            "password": REDIS_PASSWORD,
            "max_connections": POOL_MAX_CONNECTIONS,
            "socket_timeout": SOCKET_TIMEOUT,
            "socket_connect_timeout": SOCKET_CONNECT_TIMEOUT,
            "retry_on_timeout": True,
            "decode_responses": False,  # Keep as bytes for MIME data
            "protocol": 2,  # Use RESP2 protocol for better compatibility
        }

        if REDIS_SSL:
            pool_kwargs["connection_class"] = SSLConnection
            pool_kwargs["ssl_cert_reqs"] = REDIS_SSL_CERT_REQS
        else:
            pool_kwargs["connection_class"] = Connection

        self._pool = ConnectionPool(**pool_kwargs)
        self._client = redis.Redis(connection_pool=self._pool)

    @retry_on_connection_error()
    def ping(self) -> bool:
        """Test Redis connection."""
        return bool(self._client.ping())

    @retry_on_connection_error()
    def get(self, key: str) -> Optional[bytes]:
        """Get value for key with automatic retry."""
        return self._client.get(key)

    @retry_on_connection_error()
    def setex(self, key: str, time: int, value: Union[str, bytes]) -> bool:
        """Set key-value pair with expiry time."""
        return bool(self._client.setex(key, time, value))

    @retry_on_connection_error()
    def delete(self, *keys: str) -> int:
        """Delete one or more keys."""
        return int(self._client.delete(*keys))

    @retry_on_connection_error()
    def keys(self, pattern: str = "*") -> list:
        """Get keys matching pattern."""
        return self._client.keys(pattern)

    @retry_on_connection_error()
    def info(self) -> Dict[str, Any]:
        """Get Redis server information."""
        return self._client.info()

    @retry_on_connection_error()
    def exists(self, key: str) -> bool:
        """Check if a key exists."""
        return bool(self._client.exists(key))

    def get_cache_info(self) -> dict:
        """Get cache statistics and memory usage."""
        try:
            info = self.info()
            total_keys = self._client.dbsize()
            memory_used = info.get("used_memory", 0)
            total_memory = info.get("maxmemory", 0)

            hits = info.get("keyspace_hits", 0)
            misses = info.get("keyspace_misses", 0)
            total_lookups = hits + misses

            stats = {
                "total_keys": total_keys,
                "memory_used": memory_used,
                "memory_limit": total_memory,
                "memory_usage_percent": (
                    (memory_used / total_memory * 100) if total_memory else 0
                ),
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

    def close(self) -> None:
        """Close all connections in the pool."""
        self._pool.disconnect()


redis_client = RedisClient()
redis_connect = redis_client

# Separate Redis database for SNOMED data
snomed_client = RedisClient(db=2)


def get_cached_data(key: str) -> Optional[bytes]:
    """Retrieve cached data for a given key."""
    try:
        return redis_client.get(key)
    except RedisError as exc:
        logger.error("Error retrieving cached data: %s", exc)
        return None


def cache_data(key: str, value: Union[str, bytes], expiry: int = 3600) -> bool:
    """Cache data with expiry time."""
    try:
        return redis_client.setex(key, expiry, value)
    except RedisError as exc:
        logger.error("Error caching data: %s", exc)
        return False


def clear_cache(pattern: str = "*") -> bool:
    """Clear cache entries matching pattern."""
    try:
        keys = redis_client.keys(pattern)
        if keys:
            return bool(redis_client.delete(*keys))
        return True
    except RedisError as exc:
        logger.error("Error clearing cache: %s", exc)
        return False
