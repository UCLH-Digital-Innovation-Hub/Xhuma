"""
Redis Connection Module

This module provides Redis connection functionality for the Xhuma middleware service.
It implements a stateless caching strategy using Redis for temporary storage of:
- CCDA documents
- PDS lookup results
- SDS endpoint information
- NHS number to CEID mappings

The Redis connection is configured with reasonable defaults and can be customized
through environment variables.
"""

import os
from typing import Optional, Union

import redis

# Redis connection configuration
REDIS_HOST = os.getenv("REDIS_HOST", "redis")  # Changed default from localhost to redis
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_DB = int(os.getenv("REDIS_DB", 0))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)


def redis_connect() -> redis.Redis:
    """
    Creates and returns a Redis connection with configured settings.

    Returns:
        redis.Redis: Configured Redis client instance

    Note:
        Connection parameters can be customized through environment variables:
        - REDIS_HOST: Redis server hostname (default: redis)
        - REDIS_PORT: Redis server port (default: 6379)
        - REDIS_DB: Redis database number (default: 0)
        - REDIS_PASSWORD: Redis password (default: None)
    """
    try:
        client = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            db=REDIS_DB,
            password=REDIS_PASSWORD,
            decode_responses=False,  # Keep as bytes for MIME data
            socket_timeout=5,  # 5 second timeout
            socket_connect_timeout=5,
            retry_on_timeout=True,
        )
        # Test connection
        client.ping()
        return client
    except redis.ConnectionError as e:
        raise ConnectionError(f"Failed to connect to Redis: {str(e)}")


# Create a global Redis client instance
redis_client = redis_connect()


def get_cached_data(key: str) -> Optional[bytes]:
    """
    Retrieves cached data for a given key.

    Args:
        key (str): The cache key to retrieve

    Returns:
        Optional[bytes]: The cached data if found, None otherwise

    Note:
        This is a convenience wrapper around redis_client.get() that handles
        connection errors gracefully.
    """
    try:
        return redis_client.get(key)
    except redis.RedisError:
        return None


def cache_data(key: str, value: Union[str, bytes], expiry: int = 3600) -> bool:
    """
    Caches data with an optional expiry time.

    Args:
        key (str): The cache key
        value (Union[str, bytes]): The data to cache
        expiry (int): Time in seconds until the data expires (default: 1 hour)

    Returns:
        bool: True if caching was successful, False otherwise

    Note:
        Different types of data have different default expiry times:
        - CCDA documents: 4 hours
        - PDS lookups: 24 hours
        - SDS endpoints: 12 hours
    """
    try:
        return redis_client.setex(key, expiry, value)
    except redis.RedisError:
        return False


def clear_cache(pattern: str = "*") -> bool:
    """
    Clears cache entries matching the given pattern.

    Args:
        pattern (str): Pattern to match keys for deletion (default: "*" all keys)

    Returns:
        bool: True if clearing was successful, False otherwise

    Note:
        Use with caution - pattern "*" will clear all keys in the current database.
    """
    try:
        keys = redis_client.keys(pattern)
        if keys:
            return redis_client.delete(*keys)
        return True
    except redis.RedisError:
        return False


def get_cache_info() -> dict:
    """
    Retrieves information about the current cache state.

    Returns:
        dict: Dictionary containing cache statistics:
            - total_keys: Number of cached items
            - memory_used: Memory usage in bytes
            - hit_rate: Cache hit rate if available
    """
    try:
        info = redis_client.info()
        return {
            "total_keys": redis_client.dbsize(),
            "memory_used": info.get("used_memory", 0),
            "hit_rate": info.get("keyspace_hits", 0)
            / (info.get("keyspace_hits", 0) + info.get("keyspace_misses", 1)),
        }
    except redis.RedisError:
        return {"error": "Failed to retrieve cache information"}
