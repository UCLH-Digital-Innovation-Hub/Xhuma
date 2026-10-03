import asyncio
from unittest.mock import MagicMock

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from app import telemetry


@pytest.mark.parametrize(
    "error,outcome", [(None, "ok"), (ValueError("private payload"), "error"), (asyncio.CancelledError(), "cancelled")]
)
def test_stage_timing_and_error_privacy(monkeypatch, error, outcome):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(telemetry, "tracer", provider.get_tracer("test"))
    histogram = MagicMock()
    monkeypatch.setattr(telemetry, "stage_duration", histogram)
    clock = iter([10, 10.25])
    monkeypatch.setattr(telemetry, "perf_counter", lambda: next(clock))

    def run():
        with telemetry.measure("fhir.models", span_name="legacy-span-name", resource_count=478):
            if error:
                raise error

    if error:
        with pytest.raises(type(error)) as caught:
            run()
        assert caught.value is error
    else:
        run()
    (span,) = exporter.get_finished_spans()
    assert span.name == "legacy-span-name"
    assert span.attributes["resource_count"] == 478
    assert span.attributes["stage.outcome"] == outcome
    assert span.events == ()  # No raw exception messages in telemetry.
    assert span.status.status_code == (StatusCode.ERROR if error else StatusCode.UNSET)
    histogram.record.assert_called_once_with(250, {"stage": "fhir.models", "outcome": outcome})
    provider.shutdown()


def test_cache_counter_has_bounded_dimensions(monkeypatch):
    counter = MagicMock()
    monkeypatch.setattr(telemetry, "cache_lookups", counter)
    telemetry.record_cache("terminology", True)
    telemetry.record_cache("document", False)
    assert [call.args for call in counter.add.call_args_list] == [
        (1, {"cache": "terminology", "outcome": "hit"}),
        (1, {"cache": "document", "outcome": "miss"}),
    ]


@pytest.mark.asyncio
async def test_event_loop_probe_measures_delay_and_cancels(monkeypatch):
    histogram = MagicMock()
    monkeypatch.setattr(telemetry, "loop_delay", histogram)
    clock = iter([10, 10.35, 11])
    monkeypatch.setattr(telemetry, "perf_counter", lambda: next(clock))
    calls = 0

    async def sleep(interval):
        nonlocal calls
        assert interval == 0.1
        calls += 1
        if calls == 2:
            raise asyncio.CancelledError()

    monkeypatch.setattr(telemetry.asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        await telemetry.monitor_event_loop()
    assert histogram.record.call_args.args[0] == pytest.approx(250)
    histogram.record.assert_called_once()


@pytest.mark.asyncio
async def test_lifespan_stops_event_loop_probe(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from fastapi import FastAPI

    from app import main

    started = asyncio.Event()
    stopped = asyncio.Event()

    async def probe():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    engine = SimpleNamespace(dispose=AsyncMock())
    monkeypatch.setattr(main, "make_engine", MagicMock(return_value=engine))
    monkeypatch.setattr(main, "make_sessionmaker", MagicMock())
    cache = AsyncMock()
    terminology_cache = AsyncMock()
    monkeypatch.setattr(main, "redis_client", cache)
    monkeypatch.setattr(main, "snomed_client", terminology_cache)
    monkeypatch.setattr(main, "OTLPMetricExporter", MagicMock())
    monkeypatch.setattr(main, "PeriodicExportingMetricReader", MagicMock())
    monkeypatch.setattr(main, "MeterProvider", MagicMock())
    monkeypatch.setattr(main.metrics, "set_meter_provider", MagicMock())
    monkeypatch.setattr(main, "monitor_event_loop", probe)
    async with main.lifespan(FastAPI()):
        await asyncio.wait_for(started.wait(), timeout=1)
        assert not stopped.is_set()
    assert stopped.is_set()
    engine.dispose.assert_awaited_once()
    cache.close.assert_awaited_once()
    terminology_cache.close.assert_awaited_once()
    cache.setex.assert_awaited_once()
