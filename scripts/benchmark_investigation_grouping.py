"""Compare an archived grouping module with indexed grouping, without external I/O.

Before editing grouping, copy it outside the repository. Then run:
    python -m scripts.benchmark_investigation_grouping --baseline /tmp/before.py \
        --output /tmp/investigation-benchmark.json

Parsing is excluded. Timings include one shared graph build and all reports.
Only aggregate counts and measurements are emitted, never source content.
"""

import argparse
import gc
import hashlib
import importlib.util
import json
import platform
import statistics
import sys
import tracemalloc
from pathlib import Path
from time import perf_counter

from fhirclient.models.bundle import Bundle
from fhirclient.models.diagnosticreport import DiagnosticReport

from app.ccda.entries import investigation_grouping as current

FIXTURE = Path(__file__).resolve().parents[1] / "app/tests/fixtures/bundles/investigations/9465700088.json"


def load_baseline(path):
    spec = importlib.util.spec_from_file_location("investigation_benchmark_baseline", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def fixture_index(copies, fixture=FIXTURE):
    """Independent fixture copies with resource IDs and reference aliases rewritten."""
    payload = json.loads(fixture.read_text())
    index, reports = {}, []
    for copy in range(copies):
        aliases = {}
        for entry in payload["entry"]:
            resource = entry["resource"]
            reference = f"{resource['resourceType']}/{resource.get('id', 'benchmark-missing-id')}"
            aliases[reference] = f"{reference}-copy-{copy}"
            if entry.get("fullUrl"):
                aliases[entry["fullUrl"]] = f"{entry['fullUrl']}-copy-{copy}"

        def rewrite(value):
            if isinstance(value, dict):
                return {
                    key: aliases.get(item, item)
                    if key in ("reference", "fullUrl") and isinstance(item, str)
                    else rewrite(item)
                    for key, item in value.items()
                }
            if isinstance(value, list):
                return [rewrite(item) for item in value]
            return value

        copied = rewrite(payload)
        for entry in copied["entry"]:
            entry["resource"]["id"] = entry["resource"].get("id", "benchmark-missing-id") + f"-copy-{copy}"
        bundle = Bundle(copied)
        for entry in bundle.entry:
            resource = entry.resource
            index[f"{resource.resource_type}/{resource.id}"] = resource
            if entry.fullUrl:
                index[entry.fullUrl] = resource
            if isinstance(resource, DiagnosticReport):
                reports.append(resource)
    return index, reports, len(payload["entry"]) * copies


def run(module, index, reports):
    start = perf_counter()
    graph = module.build_investigation_graph(index)
    built = perf_counter()
    for report in reports:
        module.group_investigation(report, graph)
    finished = perf_counter()
    return {
        "build_ms": (built - start) * 1000,
        "group_ms": (finished - built) * 1000,
        "total_ms": (finished - start) * 1000,
    }


def allocations(module, index, reports):
    gc.collect()
    tracemalloc.start()
    graph = module.build_investigation_graph(index)
    retained, build_peak = tracemalloc.get_traced_memory()
    for report in reports:
        module.group_investigation(report, graph)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "graph_retained_KiB": round(retained / 1024, 2),
        "build_peak_KiB": round(build_peak / 1024, 2),
        "total_peak_KiB": round(peak / 1024, 2),
    }


def assert_equivalent(before, after, report, left_graph, right_graph):
    left, right = before.group_investigation(report, left_graph), after.group_investigation(report, right_graph)
    for field in ("observations", "direct", "members", "comments", "parents", "attached_comments", "issues"):
        a, b = getattr(left, field), getattr(right, field)
        assert a == b, field
        if isinstance(a, dict):
            assert list(a) == list(b), f"{field} order"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    parser.add_argument("--copies", nargs="+", type=int, default=[1, 2, 4, 8])
    parser.add_argument("--repeat", type=int, default=9)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.repeat < 1 or any(c < 1 for c in args.copies):
        parser.error("repeat and copies must be positive")
    baseline = load_baseline(args.baseline)
    rows = []
    for copies in args.copies:
        index, reports, resources = fixture_index(copies, args.fixture)
        old, new = baseline.build_investigation_graph(index), current.build_investigation_graph(index)
        for report in reports:
            assert_equivalent(baseline, current, report, old, new)
        samples = {"before": [], "after": []}
        modules = {"before": baseline, "after": current}
        for module in modules.values():
            run(module, index, reports)
        for iteration in range(args.repeat):
            # Alternate A/B order to reduce systematic warm-up/order bias.
            for name in ("before", "after") if iteration % 2 == 0 else ("after", "before"):
                gc.collect()
                samples[name].append(run(modules[name], index, reports))
        row = {"resources": resources, "reports": len(reports), "source_edges": len(new.edges)}
        for name, module in modules.items():
            row[name] = {key: round(statistics.median(s[key] for s in samples[name]), 3) for key in samples[name][0]}
            row[name].update(allocations(module, index, reports))
        row["speedup"] = round(row["before"]["total_ms"] / row["after"]["total_ms"], 2)
        rows.append(row)
    output = {
        "python": platform.python_version(),
        "repetitions": args.repeat,
        "baseline_sha256": hashlib.sha256(args.baseline.read_bytes()).hexdigest(),
        "current_sha256": hashlib.sha256(Path(current.__file__).read_bytes()).hexdigest(),
        "fixture": str(args.fixture),
        "scope": "One shared build plus all report groupings; parsed independent fixture copies; excludes parsing, rendering and external I/O. Memory is a separate tracemalloc run, excluding parsed resources.",
        "rows": rows,
    }
    serialized = json.dumps(output, indent=2) + "\n"
    if args.output:
        args.output.write_text(serialized)
    print(serialized)


if __name__ == "__main__":
    main()
