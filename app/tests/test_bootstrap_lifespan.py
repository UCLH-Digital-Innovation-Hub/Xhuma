"""Resource/state contracts at the extracted lifespan boundary."""

import asyncio
import os
import stat
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI

from app.bootstrap import lifespan as lifecycle


@pytest.mark.asyncio
@pytest.mark.parametrize("key_result", [[{"kid": "public-key"}], [], RuntimeError("key unavailable")])
async def test_resource_state_certificates_and_cleanup(monkeypatch, key_result):
    monkeypatch.setenv("NHS_CLIENT_CERT", "certificate")
    monkeypatch.setenv("NHS_CLIENT_KEY", "private key")
    monkeypatch.setenv("NHS_BUNDLE", "bundle")
    monkeypatch.setenv("CCDA_EXPIRY_HOURS", "2.5")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://collector.test:4317")
    monkeypatch.setenv("OTEL_METRIC_EXPORT_INTERVAL_MS", "7000")
    # Lifespan intentionally publishes a process environment path without restoring it.
    monkeypatch.setenv("NHS_CERT_DIR_EPHEMERAL", "before-startup")
    monkeypatch.setattr(lifecycle, "REGISTRY_ID", "registry-test-id")
    events = []

    async def close(name):
        assert not directory.exists()
        events.append(name)

    engine = SimpleNamespace(dispose=AsyncMock(side_effect=lambda: None))

    async def dispose():
        await close("engine")

    engine.dispose.side_effect = dispose
    sessionmaker = object()
    cache = AsyncMock()
    terminology = AsyncMock()

    async def close_cache():
        await close("redis")

    async def close_terminology():
        await close("snomed")

    cache.close.side_effect = close_cache
    terminology.close.side_effect = close_terminology
    # Registry publication failure remains nonfatal.
    cache.setex.side_effect = RuntimeError("Redis unavailable")
    monkeypatch.setattr(lifecycle, "redis_client", cache)
    monkeypatch.setattr(lifecycle, "snomed_client", terminology)
    monkeypatch.setattr(lifecycle, "make_engine", lambda: engine)
    make_sessionmaker = MagicMock(return_value=sessionmaker)
    monkeypatch.setattr(lifecycle, "make_sessionmaker", make_sessionmaker)
    monkeypatch.setattr(lifecycle, "fix_pem_formatting", lambda value: "normalized " + value)
    keys = MagicMock()
    if isinstance(key_result, Exception):
        keys.side_effect = key_result
    else:
        keys.return_value = key_result
    monkeypatch.setattr(lifecycle, "get_jwk_keyset", keys)
    started = asyncio.Event()

    async def probe():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            assert directory.exists()
            events.append("probe")

    with (
        patch("app.telemetry.monitor_event_loop", probe),
        patch("opentelemetry.exporter.otlp.proto.grpc.metric_exporter.OTLPMetricExporter") as exporter,
        patch("opentelemetry.sdk.metrics.export.PeriodicExportingMetricReader") as reader,
        patch("opentelemetry.sdk.metrics.MeterProvider") as provider,
        patch("opentelemetry.metrics.set_meter_provider") as set_provider,
    ):
        app = FastAPI()
        async with lifecycle.lifespan(app):
            directory = Path(os.environ["NHS_CERT_DIR_EPHEMERAL"])
            await asyncio.wait_for(started.wait(), timeout=1)
            assert app.state.engine is engine
            assert app.state.SessionLocal is sessionmaker
            assert app.state.ccda_expiry_hours == 2.5
            make_sessionmaker.assert_called_once_with(engine)
            cache.setex.assert_awaited_once_with("registry", 86400, b"registry-test-id")
            assert stat.S_IMODE(directory.stat().st_mode) == 0o700
            assert stat.S_IMODE((directory / "client_key.pem").stat().st_mode) == 0o600
            for filename, content in (
                ("client_cert.pem", "certificate"),
                ("client_key.pem", "private key"),
                ("nhs_bundle.pem", "bundle"),
            ):
                assert (directory / filename).read_text() == "normalized " + content
            expected_keys = key_result if isinstance(key_result, list) else []
            assert app.state.jwk_keys == expected_keys
            assert app.state.jwk_json == (expected_keys[0] if expected_keys else {})
            keys.assert_called_once_with()
            exporter.assert_called_once_with(endpoint="collector.test:4317", insecure=True)
            reader.assert_called_once_with(exporter=exporter.return_value, export_interval_millis=7000)
            provider.assert_called_once_with(metric_readers=[reader.return_value])
            set_provider.assert_called_once_with(provider.return_value)
        provider.return_value.shutdown.assert_not_called()
    assert events == ["probe", "snomed", "redis", "engine"]
    assert not directory.exists()
    assert os.environ["NHS_CERT_DIR_EPHEMERAL"] == str(directory)
    engine.dispose.assert_awaited_once()
    cache.close.assert_awaited_once()
    terminology.close.assert_awaited_once()
