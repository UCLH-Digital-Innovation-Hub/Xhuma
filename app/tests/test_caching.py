import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from app.caching import redis_cache


# Dummy async function to wrap
@redis_cache(ttl=60, prefix="test")
async def dummy_func(a, b, redis=None):
    return {"result": a + b}


@pytest.mark.asyncio
async def test_cache_hit(mocker):
    # Mock Redis
    mock_redis = AsyncMock()
    cached_result = {"result": 3}
    mock_redis.get.return_value = json.dumps(cached_result)

    result = await dummy_func(1, 2, redis=mock_redis)

    assert result == cached_result
    mock_redis.get.assert_called_once()
    mock_redis.set.assert_not_called()  # Because it was cached


@pytest.mark.asyncio
async def test_cache_miss_and_set(mocker):
    # Mock Redis
    mock_redis = AsyncMock()
    mock_redis.get.return_value = None  # Cache miss

    result = await dummy_func(2, 3, redis=mock_redis)

    assert result == {"result": 5}
    mock_redis.get.assert_called_once()
    mock_redis.set.assert_called_once()

    # Check that what's cached matches the return value
    set_args = mock_redis.set.call_args[0]
    cached_json = set_args[1]
    assert json.loads(cached_json) == result


@pytest.mark.asyncio
async def test_missing_redis_raises():
    with pytest.raises(ValueError) as exc:
        await dummy_func(1, 1)
    assert "Redis client must be passed" in str(exc.value)
