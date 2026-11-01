"""Lifespan-owned CPU workers with bounded admission and cancellation accounting."""

import asyncio
import math
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor

from fastapi import HTTPException

from app.telemetry import measure


class ConversionWorkers:
    """Run picklable module-level functions outside the API event loop.

    Admission is immediate: at most workers + queue_size jobs are submitted.
    Cancellation/timeouts abandon the result, but release capacity only when the
    underlying executor future completes. Running processes are never killed for
    an individual request. Shutdown drains running work off the event-loop thread.
    """

    def __init__(self, workers=1, queue_size=2, timeout=60.0):
        if workers < 1 or queue_size < 0 or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Invalid conversion worker limits")
        self.timeout = timeout
        self._capacity = workers + queue_size
        self._pending = set()
        self._closed = False
        self._pool = ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn"))

    @classmethod
    def from_environment(cls):
        return cls(
            workers=int(os.getenv("CCDA_WORKERS", "1")),
            queue_size=int(os.getenv("CCDA_QUEUE_SIZE", "2")),
            timeout=float(os.getenv("CCDA_JOB_TIMEOUT_SECONDS", "60")),
        )

    async def run(self, function, *args):
        """Submit without blocking; reject overload before retaining another job."""
        if self._closed or len(self._pending) >= self._capacity:
            raise HTTPException(503, "Document conversion capacity unavailable")
        loop = asyncio.get_running_loop()
        future = self._pool.submit(function, *args)
        self._pending.add(future)
        future.add_done_callback(lambda done: loop.call_soon_threadsafe(self._pending.discard, done))
        wrapped = asyncio.wrap_future(future)
        # Retrieve abandoned failures to avoid unhandled-future warnings. Awaiting
        # callers still receive the exception normally.
        wrapped.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        try:
            with measure("ccda.worker.wait"):
                return await asyncio.wait_for(asyncio.shield(wrapped), self.timeout)
        except asyncio.CancelledError:
            future.cancel()  # Cancels queued work only; running work keeps its slot.
            raise
        except TimeoutError:
            future.cancel()
            raise HTTPException(504, "Document conversion timed out") from None

    async def close(self):
        self._closed = True
        await asyncio.to_thread(self._pool.shutdown, wait=True, cancel_futures=True)
