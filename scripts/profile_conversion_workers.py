"""Offline comparison of serialization and CPU worker isolation (no Redis/HTTP).

Run: python -m scripts.profile_conversion_workers --repeat 5
Results are local measurements, not deployed capacity estimates. All modes use
identical synthetic dm+d enrichment; each document includes preparation and full
conversion. Pool startup is warmed separately and excluded from samples.
"""

import argparse
import asyncio
import json
import logging
import platform
import statistics
from pathlib import Path
from time import perf_counter

import xmltodict

from app.ccda.conversion import ConversionFailure, convert_document, prepare_bundle
from app.ccda.models.dmd import DMDConcept
from app.ccda.workers import ConversionWorkers

OPTIONS = {"inclusions": {}, "caching_period": "24 hours", "debug_xml": False}


def concepts(prepared):
    return {code: DMDConcept(concept_id=code, valueString="Benchmark stub") for code in prepared.dmd_codes}


def check(result):
    if isinstance(result, ConversionFailure):
        raise RuntimeError(f"Benchmark conversion failed at {result.step}: {result.exception_type}")
    return result


def inline(payload):
    prepared = check(prepare_bundle(payload))
    return check(convert_document(prepared.payload, concepts(prepared), OPTIONS))


async def worker_document(pool, payload):
    prepared = check(await pool.run(prepare_bundle, payload))
    return check(await pool.run(convert_document, prepared.payload, concepts(prepared), OPTIONS))


async def measure_batch(convert, payload):
    gaps = []

    async def heartbeat():
        previous = perf_counter()
        while True:
            await asyncio.sleep(0.005)
            now = perf_counter()
            gaps.append((now - previous) * 1000)
            previous = now

    probe = asyncio.create_task(heartbeat())
    await asyncio.sleep(0)
    start = perf_counter()
    try:
        await asyncio.gather(*(convert(payload) for _ in range(4)))
        elapsed = (perf_counter() - start) * 1000
        await asyncio.sleep(0.01)
        return elapsed, max(gaps)
    finally:
        probe.cancel()
        await asyncio.gather(probe, return_exceptions=True)


async def profile(payload, repeat):
    if not xmltodict._RUST_AVAILABLE:
        raise RuntimeError("Benchmark requires xmltodict-fast's Rust backend")
    serialization = {"python": [], "rust": []}
    totals = {"python": [], "rust": []}
    inline(payload)
    for iteration in range(repeat):
        # Alternate the order to reduce systematic warmup/order effects.
        for mode in ("python", "rust") if iteration % 2 else ("rust", "python"):
            xmltodict._RUST_AVAILABLE = mode == "rust"
            start = perf_counter()
            result = inline(payload)
            totals[mode].append((perf_counter() - start) * 1000)
            serialization[mode].append(result.timings["ccda.serialize"])
    xmltodict._RUST_AVAILABLE = True

    async def direct(payload):
        return inline(payload)

    batch_results = {}
    for count in (0, 1, 2):
        pool = ConversionWorkers(workers=count, queue_size=8) if count else None
        try:

            async def convert(payload):
                return await worker_document(pool, payload) if pool else await direct(payload)

            # Warm all workers with the same four-document workload.
            await measure_batch(convert, payload)
            samples = [await measure_batch(convert, payload) for _ in range(repeat)]
            batch_results[f"workers_{count}" if count else "inline"] = {
                "batch_of_four_ms": round(statistics.median(row[0] for row in samples), 2),
                "median_max_heartbeat_gap_ms": round(statistics.median(row[1] for row in samples), 2),
            }
        finally:
            if pool:
                await pool.close()
    return {
        "python": platform.python_version(),
        "scope": "Offline; synthetic dm+d; no external I/O; process startup excluded",
        "repetitions": repeat,
        "isolated_ms": {mode: round(statistics.median(values), 2) for mode, values in totals.items()},
        "serialization_ms": {mode: round(statistics.median(values), 2) for mode, values in serialization.items()},
        "concurrent": batch_results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--fixture", type=Path, default=Path("app/tests/fixtures/bundles/9692136744.json"))
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("repeat must be positive")
    logging.disable(logging.CRITICAL)
    result = asyncio.run(profile(args.fixture.read_bytes(), args.repeat))
    result["fixture"] = str(args.fixture)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
