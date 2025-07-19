"""
Redis Connection Module

This module provides Redis connection functionality for the Xhuma middleware service.
It implements a stateless caching strategy using Redis for temporary storage of:
- CCDA documents
- PDS lookup results
- SDS endpoint information
- NHS number to CEID mappings

The Redis connection is configured through environment variables and includes:
- Connection pooling
- Error handling
- Automatic reconnection
- Memory monitoring
"""

import logging
import os
import time
from functools import wraps
from typing import Any, Dict, Optional, Union

import redis
from redis.connection import ConnectionPool
from redis.exceptions import ConnectionError, RedisError

# Configure logging
logger = logging.getLogger(__name__)

# Redis connection configuration
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6380))
REDIS_DB = int(os.getenv("REDIS_DB", 0))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD")
REDIS_SSL = os.getenv("REDIS_SSL", "true").lower() == "true"

# Connection pool configuration
POOL_MAX_CONNECTIONS = 10
POOL_TIMEOUT = 20
SOCKET_TIMEOUT = 5
SOCKET_CONNECT_TIMEOUT = 5
MAX_RETRIES = 3
RETRY_DELAY = 1  # seconds


def retry_on_connection_error(max_retries: int = MAX_RETRIES, delay: int = RETRY_DELAY):
    """Decorator to retry Redis operations on connection errors."""

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_error = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except (ConnectionError, TimeoutError) as e:
                    last_error = e
                    if attempt < max_retries - 1:
                        time.sleep(delay)
                        logger.warning(
                            f"Retrying Redis operation, attempt {attempt + 2}/{max_retries}"
                        )
            logger.error(
                f"Redis operation failed after {max_retries} attempts: {str(last_error)}"
            )
            raise last_error

        return wrapper

    return decorator


class RedisClient:
    """Redis client with connection pooling and error handling."""

    def __init__(self):
        """Initialize Redis client with connection pool."""
        self._pool = ConnectionPool(
            host=REDIS_HOST,
            port=REDIS_PORT,
            db=REDIS_DB,
            password=REDIS_PASSWORD,
            ssl=REDIS_SSL,
            max_connections=POOL_MAX_CONNECTIONS,
            socket_timeout=SOCKET_TIMEOUT,
            socket_connect_timeout=SOCKET_CONNECT_TIMEOUT,
            retry_on_timeout=True,
            decode_responses=False,  # Keep as bytes for MIME data
            protocol=2,  # Use RESP2 protocol for better compatibility
        )
        self._client = redis.Redis(
            connection_pool=self._pool,
            ssl=REDIS_SSL,
            socket_timeout=SOCKET_TIMEOUT,
            retry_on_timeout=True,
            decode_responses=False,  # Keep as bytes for MIME data
            protocol=2,  # Use RESP2 protocol for better compatibility
        )

    @retry_on_connection_error()
    def ping(self) -> bool:
        """Test Redis connection."""
        return self._client.ping()

    @retry_on_connection_error()
    def get(self, key: str) -> Optional[bytes]:
        """Get value for key with automatic retry."""
        return self._client.get(key)

    @retry_on_connection_error()
    def setex(self, key: str, time: int, value: Union[str, bytes]) -> bool:
        """Set key-value pair with expiry time."""
        return self._client.setex(key, time, value)

    @retry_on_connection_error()
    def delete(self, *keys: str) -> int:
        """Delete one or more keys."""
        return self._client.delete(*keys)

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

            stats = {
                "total_keys": total_keys,
                "memory_used": memory_used,
                "memory_limit": total_memory,
                "memory_usage_percent": (
                    (memory_used / total_memory * 100) if total_memory else 0
                ),
                "connected_clients": info.get("connected_clients", 0),
                "hit_rate": info.get("keyspace_hits", 0)
                / (info.get("keyspace_hits", 0) + info.get("keyspace_misses", 1)),
            }

            # Log warning if memory usage is high
            if stats["memory_usage_percent"] > 80:
                logger.warning(
                    f"Redis memory usage is high: {stats['memory_usage_percent']:.1f}%"
                )

            return stats
        except RedisError as e:
            logger.error(f"Failed to retrieve cache information: {str(e)}")
            return {"error": str(e)}

    def close(self):
        """Close all connections in the pool."""
        self._pool.disconnect()


# Create global Redis client instance
redis_client = RedisClient()

# Export the redis_connect instance for use in other modules
redis_connect = redis_client


def get_cached_data(key: str) -> Optional[bytes]:
    """Retrieve cached data for a given key."""
    try:
        return redis_client.get(key)
    except RedisError as e:
        logger.error(f"Error retrieving cached data: {str(e)}")
        return None


def cache_data(key: str, value: Union[str, bytes], expiry: int = 3600) -> bool:
    """Cache data with expiry time."""
    try:
        return redis_client.setex(key, expiry, value)
    except RedisError as e:
        logger.error(f"Error caching data: {str(e)}")
        return False


def clear_cache(pattern: str = "*") -> bool:
    """Clear cache entries matching pattern."""
    try:
        keys = redis_client.keys(pattern)
        if keys:
            return bool(redis_client.delete(*keys))
        return True
    except RedisError as e:
        logger.error(f"Error clearing cache: {str(e)}")
        return False
