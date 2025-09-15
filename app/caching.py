import functools
import hashlib
import json
from typing import Any, Callable

from .redis_connect import redis_client


async def get_or_set_cache(key: str, fetch_fn: Callable[[], Any], ttl: int = 300):
    """
    Get a value from Redis cache or set it if not present.

    :param key: Cache key
    :param fetch_fn: Function to fetch the value if not in cache
    :param ttl: Time to live for the cache entry in seconds

    :return: Cached or fetched value"""
    cached = await redis_client.get(key)
    if cached:
        return json.loads(cached)

    result = await fetch_fn()
    await redis_client.setex(key, json.dumps(result), ex=ttl)
    return result


def hash_key(prefix, *args, **kwargs):
    """
    Generate a SHA-256 hash key based on the provided arguments.
    :param prefix: Prefix for the key
    :param args: Positional arguments
    :param kwargs: Keyword arguments
    :return: A string key"""
    key_input = json.dumps({"args": args, "kwargs": kwargs}, sort_keys=True)
    hash_digest = hashlib.sha256(key_input.encode()).hexdigest()
    return f"{prefix}:{hash_digest}"


def redis_cache(ttl: int = 300, prefix: str = "", redis=redis_client):
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, redis=None, **kwargs):
            if redis is None:
                raise ValueError(
                    "Redis client must be passed as a keyword argument: redis=..."
                )

            # Create a unique cache key based on the function name and args
            key_input = json.dumps(
                {"args": args, "kwargs": kwargs}, sort_keys=True, default=str
            )
            hash_digest = hashlib.sha256(key_input.encode()).hexdigest()
            cache_key = f"{prefix or func.__name__}:{hash_digest}"

            # Check Redis
            cached = await redis.get(cache_key)
            if cached:
                return json.loads(cached)

            # Call the function and cache the result
            result = await func(*args, **kwargs)
            await redis.set(cache_key, json.dumps(result), ex=ttl)
            return result

        return wrapper

    return decorator
