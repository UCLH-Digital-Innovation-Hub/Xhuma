"""Low-cardinality performance measurements without clinical identifiers or payloads."""

import asyncio
from contextlib import contextmanager
from time import perf_counter

from opentelemetry import metrics, trace

meter = metrics.get_meter("xhuma.pipeline")
tracer = trace.get_tracer("xhuma.pipeline")
stage_duration = meter.create_histogram("xhuma.stage.duration", unit="ms", description="Pipeline stage elapsed time")
cache_lookups = meter.create_counter("xhuma.cache.lookups", description="Cache lookups by cache and hit/miss outcome")
loop_delay = meter.create_histogram("xhuma.event_loop.delay", unit="ms", description="Event-loop scheduling delay")


@contextmanager
def measure(stage: str, *, span_name: str | None = None, **attributes):
    """Measure fixed stage names; callers may attach counts, never patient data."""
    started = perf_counter()
    outcome = "ok"
    with tracer.start_as_current_span(
        span_name or f"xhuma.{stage}", attributes=attributes, record_exception=False, set_status_on_exception=False
    ) as span:
        try:
            yield span
        except BaseException as exc:
            outcome = "cancelled" if isinstance(exc, asyncio.CancelledError) else "error"
            span.set_status(trace.Status(trace.StatusCode.ERROR))
            raise
        finally:
            elapsed = (perf_counter() - started) * 1000
            span.set_attribute("stage.outcome", outcome)
            span.set_attribute("stage.duration_ms", elapsed)
            stage_duration.record(elapsed, {"stage": stage, "outcome": outcome})


def record_cache(cache: str, hit: bool):
    cache_lookups.add(1, {"cache": cache, "outcome": "hit" if hit else "miss"})


async def monitor_event_loop(interval: float = 0.1):
    """One cancellable probe per application worker, started by app lifespan."""
    while True:
        started = perf_counter()
        await asyncio.sleep(interval)
        loop_delay.record(max(0.0, perf_counter() - started - interval) * 1000)
