import asyncio
import importlib
import shutil
import subprocess
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from redis.exceptions import ConnectionError, ResponseError, TimeoutError

cache_module = importlib.import_module("app.redis_connect")


@pytest.fixture
def backend(monkeypatch):
    client = AsyncMock()
    client.pipeline = MagicMock()
    factory = MagicMock(return_value=client)
    monkeypatch.setattr(cache_module.redis, "Redis", factory)
    return SimpleNamespace(client=client, factory=factory)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,args,result,expected",
    [
        ("ping", (), True, True),
        ("get", ("key",), b"\x00document\xff", b"\x00document\xff"),
        ("get", ("missing",), None, None),
        ("setex", ("key", timedelta(hours=4), b"value"), True, True),
        ("delete", ("one", "two"), 2, 2),
        ("keys", ("prefix:*",), [b"prefix:one"], [b"prefix:one"]),
        ("info", (), {"used_memory": 100}, {"used_memory": 100}),
        ("exists", ("key",), 1, True),
    ],
)
async def test_commands_await_redis_and_preserve_values(backend, method, args, result, expected):
    getattr(backend.client, method).return_value = result
    client = cache_module.RedisClient(db=2)
    assert await getattr(client, method)(*args) == expected
    getattr(backend.client, method).assert_awaited_once_with(*args)
    kwargs = backend.factory.call_args.kwargs
    assert kwargs["db"] == 2
    assert kwargs["decode_responses"] is False
    assert kwargs["protocol"] == 2
    assert kwargs["max_connections"] == cache_module.POOL_MAX_CONNECTIONS


@pytest.mark.asyncio
async def test_retries_await_backoff_and_stop_after_three_attempts(backend, monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr(cache_module.asyncio, "sleep", sleep)
    backend.client.get.side_effect = [ConnectionError("unavailable"), TimeoutError("timeout"), b"cached"]
    client = cache_module.RedisClient()
    assert await client.get("key") == b"cached"
    assert backend.client.get.await_count == 3
    assert [call.args for call in sleep.await_args_list] == [(1,), (1,)]

    error = ConnectionError("still unavailable")
    backend.client.get.reset_mock(side_effect=True)
    backend.client.get.side_effect = error
    sleep.reset_mock()
    with pytest.raises(ConnectionError) as caught:
        await client.get("key")
    assert caught.value is error
    assert backend.client.get.await_count == 3
    assert sleep.await_count == 2


@pytest.mark.asyncio
async def test_nonconnection_errors_are_not_retried(backend, monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr(cache_module.asyncio, "sleep", sleep)
    backend.client.get.side_effect = ResponseError("WRONGTYPE")
    with pytest.raises(ResponseError):
        await cache_module.RedisClient().get("key")
    backend.client.get.assert_awaited_once()
    sleep.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("during_backoff", [False, True])
async def test_waits_yield_to_other_tasks_and_allow_cancellation(backend, monkeypatch, during_backoff):
    waiting = asyncio.Event()
    release = asyncio.Event()

    async def pause(*args):
        waiting.set()
        await release.wait()

    if during_backoff:
        backend.client.get.side_effect = ConnectionError("retry")
        monkeypatch.setattr(cache_module.asyncio, "sleep", pause)
    else:
        backend.client.get.side_effect = pause
    task = asyncio.create_task(cache_module.RedisClient().get("key"))
    try:
        # This task must run while the other one is waiting in Redis or retry backoff.
        await asyncio.wait_for(waiting.wait(), timeout=1)
        assert not task.done()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    backend.client.get.assert_awaited_once()


@pytest.mark.asyncio
async def test_stats_and_close_await_underlying_operations(backend):
    backend.client.info.return_value = {
        "used_memory": 90,
        "maxmemory": 100,
        "keyspace_hits": 3,
        "keyspace_misses": 1,
        "connected_clients": 2,
    }
    backend.client.dbsize.return_value = 8
    client = cache_module.RedisClient()
    assert await client.get_cache_info() == {
        "total_keys": 8,
        "memory_used": 90,
        "memory_limit": 100,
        "memory_usage_percent": 90,
        "connected_clients": 2,
        "hit_rate": 0.75,
    }
    backend.client.info.assert_awaited_once()
    backend.client.dbsize.assert_awaited_once()
    await client.close()
    backend.client.aclose.assert_awaited_once_with(close_connection_pool=True)


@pytest.mark.asyncio
async def test_cache_helpers_preserve_error_and_empty_cache_behavior(monkeypatch):
    client = AsyncMock()
    monkeypatch.setattr(cache_module, "redis_client", client)
    client.get.return_value = b"value"
    client.setex.return_value = True
    client.keys.return_value = [b"one", b"two"]
    client.delete.return_value = 2
    assert await cache_module.get_cached_data("key") == b"value"
    assert await cache_module.cache_data("key", b"value", expiry=45)
    client.setex.assert_awaited_once_with("key", 45, b"value")
    assert await cache_module.clear_cache("prefix:*")
    client.delete.assert_awaited_once_with(b"one", b"two")
    client.keys.return_value = []
    client.delete.reset_mock()
    assert await cache_module.clear_cache()
    client.delete.assert_not_awaited()
    for method in ["get", "setex", "keys"]:
        getattr(client, method).side_effect = ConnectionError("offline")
    assert await cache_module.get_cached_data("key") is None
    assert await cache_module.cache_data("key", "value") is False
    assert await cache_module.clear_cache() is False


@pytest.mark.asyncio
async def test_real_redis_bytes_expiry_database_isolation_and_atomic_pipeline(tmp_path, monkeypatch):
    """Optional local integration check: isolated Unix socket, no shared Redis instance."""
    executable = shutil.which("redis-server")
    if not executable:
        pytest.skip("redis-server is not installed")
    socket_path = tmp_path / "redis.sock"
    server = subprocess.Popen(
        [executable, "--port", "0", "--unixsocket", str(socket_path), "--save", "", "--appendonly", "no"],
        cwd=tmp_path,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    original_redis = cache_module.redis.Redis
    monkeypatch.setattr(cache_module, "REDIS_SSL", False)
    monkeypatch.setattr(cache_module, "REDIS_PASSWORD", None)
    monkeypatch.setattr(
        cache_module.redis, "Redis", lambda **kwargs: original_redis(unix_socket_path=str(socket_path), **kwargs)
    )
    document_cache = cache_module.RedisClient(db=0)
    terminology_cache = cache_module.RedisClient(db=2)
    try:

        async def ready():
            while not socket_path.exists():
                if server.poll() is not None:
                    raise AssertionError("Temporary Redis server exited before startup")
                await asyncio.sleep(0.01)

        await asyncio.wait_for(ready(), timeout=5)
        assert await document_cache.ping()
        assert await terminology_cache.setex("concept", 30, b"cached term")
        assert await document_cache.get("concept") is None
        assert await terminology_cache.get("concept") == b"cached term"
        async with document_cache.pipeline() as pipe:
            # Enqueue without await; nothing is sent until execute().
            pipe.setex("patient", timedelta(seconds=30), "document")
            pipe.setex("document", timedelta(seconds=30), b"\x00mime\xff")
            pipe.setex("doc_patient:document", timedelta(seconds=30), "patient")
            assert await document_cache.get("patient") is None
            assert pipe.is_transaction
            assert await pipe.execute() == [True, True, True]
        assert await document_cache.get("patient") == b"document"
        assert await document_cache.get("document") == b"\x00mime\xff"
        assert await document_cache.get("doc_patient:document") == b"patient"
        for key in ["patient", "document", "doc_patient:document"]:
            assert 0 < await document_cache._client.ttl(key) <= 30
        # Abandoning a queued transaction must release state without publishing it.
        with pytest.raises(ValueError):
            async with document_cache.pipeline() as pipe:
                pipe.setex("abandoned", 30, "value")
                raise ValueError("cancel before publishing")
        assert await document_cache.get("abandoned") is None
        assert await document_cache.delete("patient", "document", "doc_patient:document") == 3
    finally:
        await document_cache.close()
        await terminology_cache.close()
        server.terminate()
        server.wait(timeout=5)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_phase", ["startup", "shutdown"])
async def test_lifespan_closes_both_caches_and_database_after_failure(monkeypatch, failure_phase):
    from fastapi import FastAPI

    from app import main

    document_cache = AsyncMock()
    terminology_cache = AsyncMock()
    engine = SimpleNamespace(dispose=AsyncMock())
    monkeypatch.setattr(main, "redis_client", document_cache)
    monkeypatch.setattr(main, "snomed_client", terminology_cache)
    monkeypatch.setattr(main, "make_engine", lambda: engine)
    monkeypatch.setattr(main, "make_sessionmaker", MagicMock())
    monkeypatch.setattr(main, "OTLPMetricExporter", MagicMock())
    monkeypatch.setattr(main, "PeriodicExportingMetricReader", MagicMock())
    monkeypatch.setattr(main, "MeterProvider", MagicMock())
    monkeypatch.setattr(main.metrics, "set_meter_provider", MagicMock())
    if failure_phase == "startup":
        main.OTLPMetricExporter.side_effect = RuntimeError("startup failed")
    else:
        terminology_cache.close.side_effect = RuntimeError("shutdown failed")
    with pytest.raises(RuntimeError, match=f"{failure_phase} failed"):
        async with main.lifespan(FastAPI()):
            assert failure_phase == "shutdown"
    document_cache.setex.assert_awaited_once()
    document_cache.close.assert_awaited_once()
    terminology_cache.close.assert_awaited_once()
    engine.dispose.assert_awaited_once()
