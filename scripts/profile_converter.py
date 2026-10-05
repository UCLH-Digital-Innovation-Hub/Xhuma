"""Offline CPU/allocation profile; excludes HTTP, Redis, audit I/O and real terminology work.

Run from the repository root: python -m scripts.profile_converter --output /tmp/converter-profile.json
"""

import argparse
import asyncio
import cProfile
import gc
import importlib
import json
import logging
import platform
import socket
import statistics
import tracemalloc
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

from fhirclient.models.bundle import Bundle

from app.ccda.convert_mime import base64_xml
from app.ccda.fhir2ccda import convert_bundle
from app.ccda.models.dmd import DMDConcept

FIXTURES = Path(__file__).resolve().parents[1] / "app/tests/fixtures/bundles"
DEFAULT_FIXTURES = [
    "9690937472.json",
    "9692136744.json",
    "9692140466.json",
    "investigations/9465700088.json",
    "investigations/9692136744.json",
]


async def stub_terminology(concept_id):
    return DMDConcept(concept_id=concept_id, valueString="Profile stub")


async def pipeline(payload):
    started = perf_counter()
    parsed = json.loads(payload)
    decoded = perf_counter()
    bundle = Bundle(parsed)
    modeled = perf_counter()
    index = {}
    for entry in bundle.entry or []:
        index[f"{entry.resource.resource_type}/{entry.resource.id}"] = entry.resource
        if entry.fullUrl:
            index[entry.fullUrl] = entry.resource
    indexed = perf_counter()
    document = await convert_bundle(bundle, index)
    converted = perf_counter()
    encoded = base64_xml(document)
    serialized = perf_counter()
    return {
        "resources": len(bundle.entry or []),
        "base64_KiB": len(encoded) / 1024,
        "stages_ms": dict(
            zip(
                ["json", "models", "index", "convert", "xml_base64"],
                [
                    (b - a) * 1000
                    for a, b in zip(
                        [started, decoded, modeled, indexed, converted],
                        [decoded, modeled, indexed, converted, serialized],
                        strict=True,
                    )
                ],
                strict=True,
            )
        ),
        "total_ms": (serialized - started) * 1000,
    }


async def concurrent_profile(payload):
    gaps = []

    async def heartbeat():
        previous = perf_counter()
        while True:
            await asyncio.sleep(0.005)
            current = perf_counter()
            gaps.append((current - previous) * 1000)
            previous = current

    probe = asyncio.create_task(heartbeat())
    await asyncio.sleep(0)  # Start the probe before scheduling conversion work.
    started = perf_counter()
    await asyncio.gather(*(pipeline(payload) for _ in range(4)))
    elapsed = (perf_counter() - started) * 1000
    await asyncio.sleep(0.01)
    probe.cancel()
    try:
        await probe
    except asyncio.CancelledError:
        pass
    return {"batch_of_four_ms": round(elapsed, 2), "max_heartbeat_gap_ms": round(max(gaps), 2)}


async def profile(args):
    rows = []
    for fixture in args.fixtures:
        payload = (FIXTURES / fixture).read_text()
        await pipeline(payload)  # Warm imports and one-time model setup.
        samples = []
        for _ in range(args.repeat):
            gc.collect()
            samples.append(await pipeline(payload))
        gc.collect()
        tracemalloc.start()
        await pipeline(payload)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        row = {
            "fixture": fixture,
            "resources": samples[0]["resources"],
            "base64_KiB": round(samples[0]["base64_KiB"]),
            "stages_ms": {
                key: round(statistics.median(s["stages_ms"][key] for s in samples), 2)
                for key in samples[0]["stages_ms"]
            },
            "total_ms": round(statistics.median(s["total_ms"] for s in samples), 2),
            "peak_python_MiB": round(peak / 1024 / 1024, 2),
            "concurrent": await concurrent_profile(payload),
        }
        rows.append(row)
    if args.cprofile:
        profiler = cProfile.Profile()
        profiler.enable()
        await pipeline((FIXTURES / args.fixtures[0]).read_text())
        profiler.disable()
        profiler.dump_stats(args.cprofile)
    return {
        "python": platform.python_version(),
        "repetitions": args.repeat,
        "terminology": "stubbed; network disabled",
        "rows": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixtures", nargs="*", default=DEFAULT_FIXTURES)
    parser.add_argument("--repeat", type=int, default=7)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cprofile", type=Path)
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be positive")
    logging.disable(logging.CRITICAL)
    medication = importlib.import_module("app.ccda.entries.medication")
    with (
        patch.object(medication, "dmd_lookup", stub_terminology),
        patch.object(
            socket.socket, "connect", side_effect=RuntimeError("Network is disabled during offline profiling")
        ),
        patch.object(
            socket.socket, "connect_ex", side_effect=RuntimeError("Network is disabled during offline profiling")
        ),
    ):
        result = asyncio.run(profile(args))
    output = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(output + "\n")
    print(output)


if __name__ == "__main__":
    main()
