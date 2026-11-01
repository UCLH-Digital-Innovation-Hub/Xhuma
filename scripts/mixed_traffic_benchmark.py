"""Controlled local HTTP load: cached ITI-39 alongside fresh document conversion.

Never point this harness at a shared service. It creates its own Redis process
and PostgreSQL container, and only truncates those disposable stores. NHS calls
are replaced by a fixed repository test fixture; SOAP retrieval, conversion,
Redis publication, and PostgreSQL auditing run real application code.

Run with --baseline pointing to an isolated checkout with its locked .venv.
The load generator runs separately from each single-process Uvicorn server.
"""

import argparse
import asyncio
import base64
import hashlib
import json
import logging
import math
import os
import random
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODES = ("dev", "inline", "workers_1", "workers_2")
SUCCESS = b'status="urn:oasis:names:tc:ebxml-regrep:ResponseStatusType:Success"'


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def retrieval_body():
    """Synthetic SAML context and a fixed preloaded document, no real identity."""
    attributes = {
        "urn:oasis:names:tc:xspa:1.0:subject:subject-id": "synthetic-benchmark",
        "urn:oasis:names:tc:xspa:1.0:subject:organization": "Synthetic benchmark",
        "urn:oasis:names:tc:xspa:1.0:subject:organization-id": "BENCH",
        "urn:oasis:names:tc:xacml:2.0:resource:resource-id": "9692136744",
    }
    attrs = "".join(
        f'<saml:Attribute Name="{name}"><saml:AttributeValue>{value}</saml:AttributeValue></saml:Attribute>'
        for name, value in attributes.items()
    )
    attrs += '<saml:Attribute Name="urn:oasis:names:tc:xacml:2.0:subject:role"><saml:AttributeValue><Role code="224608005" codeSystem="2.16.840.1.113883.6.96"/></saml:AttributeValue></saml:Attribute>'
    return (
        '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
        'xmlns:a="http://www.w3.org/2005/08/addressing" '
        'xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd" '
        'xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion">'
        "<s:Header><a:MessageID>urn:uuid:00000000-0000-0000-0000-000000000001</a:MessageID>"
        "<a:Action>urn:ihe:iti:2007:CrossGatewayRetrieve</a:Action>"
        "<a:ReplyTo><a:Address>http://www.w3.org/2005/08/addressing/anonymous</a:Address></a:ReplyTo>"
        '<wsse:Security><saml:Assertion ID="synthetic" Version="2.0"><saml:Issuer>urn:nhs:names:services:spine</saml:Issuer>'
        f"<saml:AttributeStatement>{attrs}</saml:AttributeStatement></saml:Assertion></wsse:Security></s:Header>"
        '<s:Body><RetrieveDocumentSetRequest xmlns="urn:ihe:iti:xds-b:2007"><DocumentRequest>'
        "<HomeCommunityId>urn:oid:1.2.3</HomeCommunityId><RepositoryUniqueId>1.2.3</RepositoryUniqueId>"
        "<DocumentUniqueId>benchmark-cached</DocumentUniqueId>"
        "</DocumentRequest></RetrieveDocumentSetRequest></s:Body></s:Envelope>"
    ).encode()


def make_app(args):
    # Import only after selecting the source checkout, also inherited by spawn.
    sys.path.insert(0, str(args.source))
    import httpx
    import xmltodict
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    from sqlalchemy import text
    from sqlmodel import SQLModel

    from app.audit import db_models  # noqa: F401 - register SQLModel metadata
    from app.audit.models import SAMLAttributes
    from app.ccda import dmd
    from app.ccda.models.datatypes import CD
    from app.ccda.models.dmd import DMDConcept, VPIProperty
    from app.db import make_engine, make_sessionmaker
    from app.gpconnect_stages.document import process_document
    from app.gpconnect_stages.types import DiagnosticStep
    from app.redis_connect import redis_client, snomed_client
    from app.soap import soap

    payload = args.fixture.read_bytes()
    parsed = json.loads(payload)
    # Seed a superset of medication codes. This doesn't change runtime selection.
    codes = {
        int(coding["code"])
        for entry in parsed["entry"]
        if entry.get("resource", {}).get("resourceType") == "Medication"
        for coding in entry["resource"].get("code", {}).get("coding", [])
        if str(coding.get("code", "")).isdigit()
    }
    saml = SAMLAttributes(
        subject_id="synthetic-benchmark",
        organization="Synthetic benchmark",
        organization_id="BENCH",
        role=CD(code="224608005", codeSystem="2.16.840.1.113883.6.96"),
    )
    upstream = httpx.Response(200, content=payload)
    metrics = {"new_started": 0, "new_success": 0, "terminology_fallbacks": 0}

    async def forbid_fallback(*args, **kwargs):
        metrics["terminology_fallbacks"] += 1
        raise RuntimeError("Benchmark requires prewarmed terminology; no external HTTP permitted")

    dmd._resolve_dmd_concept = forbid_fallback

    class InlineWorkers:
        async def run(self, function, *args):
            return function(*args)

    @asynccontextmanager
    async def lifespan(app):
        engine = make_engine()
        pool = None
        try:
            async with engine.begin() as connection:
                await connection.run_sync(SQLModel.metadata.create_all)
                await connection.execute(text("CREATE SEQUENCE IF NOT EXISTS audit_event_seq"))
                await connection.execute(text("TRUNCATE audit_event"))
            app.state.SessionLocal = make_sessionmaker(engine)
            app.state.ccda_expiry_hours = 1
            if args.mode == "inline":
                app.state.conversion_workers = InlineWorkers()
            elif args.mode.startswith("workers_"):
                from app.ccda.workers import ConversionWorkers

                pool = ConversionWorkers(workers=int(args.mode[-1]), queue_size=2, timeout=60)
                app.state.conversion_workers = pool
            # Dedicated Redis owned by this harness, never an existing cache.
            await redis_client._client.flushdb()
            await snomed_client._client.flushdb()
            await snomed_client.setex(dmd.ACTIVE_RELEASE_KEY, 3600, "benchmark-release")
            for code in codes:
                concept = DMDConcept(
                    concept_id=code, valueString="Synthetic medicine", vpi=VPIProperty(value=5, unit="mg")
                )
                await snomed_client.setex(
                    dmd.dmd_resolved_key(code, "benchmark-release"), 3600, concept.model_dump_json()
                )
            request = Request(
                {
                    "type": "http",
                    "method": "POST",
                    "path": "/benchmark/new",
                    "headers": [],
                    "app": app,
                    "client": ("127.0.0.1", 1),
                    "server": ("127.0.0.1", args.port),
                    "scheme": "http",
                }
            )
            doc_id = await process_document(
                upstream,
                9692136744,
                saml,
                ssp_trace_id=None,
                request=request,
                log_dir=None,
                step=DiagnosticStep("start"),
            )
            if not isinstance(doc_id, str):
                raise RuntimeError("Warmup conversion failed")
            encoded, nhs = await redis_client.hmget(f"document:{doc_id}", "content", "nhs_number")
            assert encoded and b"ClinicalDocument" in base64.b64decode(encoded)
            await redis_client._client.hset(
                "document:benchmark-cached", mapping={"content": encoded, "nhs_number": nhs}
            )
            app.state.cached_token = encoded[:48].decode()
            app.state.cached_bytes = len(encoded)
            yield
        finally:
            if pool:
                await pool.close()
            await engine.dispose()
            await redis_client.close()
            await snomed_client.close()

    app = FastAPI(lifespan=lifespan)
    app.include_router(soap.router)
    soap.register_handlers(app)

    @app.get("/benchmark/status")
    async def status():
        async with app.state.SessionLocal() as session:
            rows = await session.execute(text("SELECT action, count(*) FROM audit_event GROUP BY action"))
            audit_counts = dict(rows.all())
        return {
            "audit_counts": audit_counts,
            "python": sys.version.split()[0],
            "cpu_affinity": len(os.sched_getaffinity(0)),
            **metrics,
            "cached_token": app.state.cached_token,
            "cached_bytes": app.state.cached_bytes,
            "rust": bool(getattr(xmltodict, "_RUST_AVAILABLE", False)),
        }

    @app.post("/benchmark/new")
    async def new_document(request: Request):
        metrics["new_started"] += 1
        # Same small stand-in for upstream network latency in all four modes.
        await asyncio.sleep(0.02)
        result = await process_document(
            upstream, 9692136744, saml, ssp_trace_id=None, request=request, log_dir=None, step=DiagnosticStep("start")
        )
        if isinstance(result, str):
            metrics["new_success"] += 1
            return JSONResponse({"success": True, "document_id": result})
        return result

    return app


def percentile(values, fraction):
    """Nearest-rank percentile: retain individual samples, not bucket estimates."""
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(len(ordered) * fraction) - 1)], 2) if ordered else None


async def traffic(base_url, rate, duration, seed):
    import httpx

    limits = httpx.Limits(max_connections=512, max_keepalive_connections=64)
    async with httpx.AsyncClient(base_url=base_url, timeout=60, limits=limits, trust_env=False) as client:
        status = (await client.get("/benchmark/status")).json()
        token = status["cached_token"].encode()
        body = retrieval_body()

        async def request(kind, due):
            started = time.perf_counter()
            try:
                response = await client.post(
                    "/SOAP/iti39" if kind == "cached" else "/benchmark/new",
                    content=body if kind == "cached" else b"",
                    headers={"Content-Type": "application/soap+xml"},
                )
                finished = time.perf_counter()
                valid = response.status_code == 200
                if valid:
                    valid = (
                        (SUCCESS in response.content and token in response.content)
                        if kind == "cached"
                        else response.json().get("success") is True
                    )
                return {
                    "kind": kind,
                    "ok": valid,
                    "status": response.status_code,
                    "latency_ms": (finished - due) * 1000,
                    "dispatch_lag_ms": (started - due) * 1000,
                    "finished": finished,
                }
            except httpx.HTTPError as exc:
                finished = time.perf_counter()
                return {
                    "kind": kind,
                    "ok": False,
                    "status": type(exc).__name__,
                    "latency_ms": (finished - due) * 1000,
                    "dispatch_lag_ms": (started - due) * 1000,
                    "finished": finished,
                }

        # Warm connections, SOAP parsing and worker processes before measurement.
        warm = await asyncio.gather(
            *(request(kind, time.perf_counter()) for kind in ["new", "new", "cached", "cached"])
        )
        if not all(row["ok"] for row in warm):
            raise RuntimeError(f"Warmup failed: {[(r['kind'], r['status'], r['ok']) for r in warm]}")
        before = (await client.get("/benchmark/status")).json()
        rng = random.Random(seed)
        kinds = []
        for _ in range(math.ceil(rate * duration / 5)):
            block = ["cached"] * 4 + ["new"]
            rng.shuffle(block)
            kinds.extend(block)
        kinds = kinds[: int(rate * duration)]
        start = time.perf_counter() + 0.05
        tasks = []
        for number, kind in enumerate(kinds):
            due = start + number / rate
            await asyncio.sleep(max(0, due - time.perf_counter()))
            tasks.append(asyncio.create_task(request(kind, due)))
        samples = await asyncio.gather(*tasks)
        end = max(start + duration, max(row["finished"] for row in samples))
        after = (await client.get("/benchmark/status")).json()
        fallback_delta = after["terminology_fallbacks"] - before["terminology_fallbacks"]
        if fallback_delta:
            raise RuntimeError(f"Unexpected terminology fallbacks: {fallback_delta}")
        summary = {}
        for kind in ("cached", "new"):
            rows = [r for r in samples if r["kind"] == kind]
            successful = [r for r in rows if r["ok"]]
            counts = {}
            for row in rows:
                key = str(row["status"])
                counts[key] = counts.get(key, 0) + 1
            latencies = [r["latency_ms"] for r in successful]
            summary[kind] = {
                "offered": len(rows),
                "successful": len(successful),
                "failed": len(rows) - len(successful),
                "successful_rps_including_drain": round(len(successful) / (end - start), 3),
                "successful_rps_during_arrivals": round(
                    sum(r["finished"] <= start + duration for r in successful) / duration, 3
                ),
                "p50_ms": percentile(latencies, 0.5),
                "p95_ms": percentile(latencies, 0.95),
                "p99_ms": percentile(latencies, 0.99),
                "status_counts": counts,
            }
        return {
            "rate": rate,
            "duration_seconds": duration,
            "elapsed_including_drain": round(end - start, 3),
            "summary": summary,
            "dispatch_lag_p99_ms": percentile([r["dispatch_lag_ms"] for r in samples], 0.99),
            "metadata": {
                "cached_bytes": after["cached_bytes"],
                "rust": after["rust"],
                "new_conversions": after["new_success"] - before["new_success"],
                "audit_counts": {
                    key: value - before["audit_counts"].get(key, 0) for key, value in after["audit_counts"].items()
                },
                "python": after["python"],
                "cpu_affinity": after["cpu_affinity"],
                "terminology_fallbacks": fallback_delta,
            },
            "samples": [{k: v for k, v in row.items() if k != "finished"} for row in samples],
        }


def stop_process(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


async def run(args):
    import httpx

    results = {
        "baseline_sha": args.baseline_sha,
        "fixture_sha256": hashlib.sha256(args.fixture.read_bytes()).hexdigest(),
        "mix": "80% cached ITI-39 / 20% new conversion",
        "runs": [],
    }
    with tempfile.TemporaryDirectory(prefix="xhuma-mixed-") as directory:
        temporary = Path(directory)
        redis_port, db_port = free_port(), free_port()
        container = f"xhuma-mixed-{uuid.uuid4().hex[:10]}"
        redis_log = (temporary / "redis.log").open("w")
        redis = subprocess.Popen(
            [
                "redis-server",
                "--bind",
                "127.0.0.1",
                "--port",
                str(redis_port),
                "--save",
                "",
                "--appendonly",
                "no",
                "--dir",
                directory,
            ],
            stdout=redis_log,
            stderr=subprocess.STDOUT,
        )
        container_created = False
        try:
            subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-d",
                    "--name",
                    container,
                    "-p",
                    f"127.0.0.1:{db_port}:5432",
                    "-e",
                    "POSTGRES_USER=benchmark",
                    "-e",
                    "POSTGRES_PASSWORD=benchmark-local-only",
                    "-e",
                    "POSTGRES_DB=benchmark",
                    "postgres:15",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            container_created = True
            for _ in range(60):
                check = subprocess.run(
                    ["docker", "exec", container, "pg_isready", "-U", "benchmark"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if check.returncode == 0:
                    break
                await asyncio.sleep(0.5)
            else:
                raise RuntimeError("Isolated PostgreSQL did not start")
            for repeat in range(args.repeats):
                modes = list(MODES)
                random.Random(31 + repeat).shuffle(modes)
                for mode in modes:
                    source = args.baseline if mode == "dev" else ROOT
                    python = source / ".venv/bin/python"
                    for rate in args.rates:
                        port = free_port()
                        env = {
                            "PATH": os.environ["PATH"],
                            "HOME": os.environ["HOME"],
                            "PYTHONWARNINGS": "ignore",
                            "ENV": "int",
                            "API_KEY": "synthetic-benchmark-only",
                            "ORG_ASID": "123",
                            "ORG_CODE": "BENCH",
                            "REGISTRY_ID": "1.2.3",
                            "SAML_TRUSTED_ISSUER": "urn:nhs:names:services:spine",
                            "REDIS_HOST": "127.0.0.1",
                            "REDIS_PORT": str(redis_port),
                            "DATABASE_URL": f"postgresql+asyncpg://benchmark:benchmark-local-only@127.0.0.1:{db_port}/benchmark",
                        }
                        log_path = temporary / f"{mode}-{repeat}-{rate}.log"
                        with log_path.open("w") as log:
                            server = subprocess.Popen(
                                [
                                    str(python),
                                    str(Path(__file__).resolve()),
                                    "--server",
                                    "--source",
                                    str(source),
                                    "--mode",
                                    mode,
                                    "--fixture",
                                    str(args.fixture),
                                    "--port",
                                    str(port),
                                ],
                                cwd=directory,
                                env=env,
                                stdout=log,
                                stderr=subprocess.STDOUT,
                            )
                            try:
                                url = f"http://127.0.0.1:{port}"
                                async with httpx.AsyncClient(timeout=1, trust_env=False) as probe:
                                    for _ in range(120):
                                        if server.poll() is not None:
                                            raise RuntimeError(
                                                "Benchmark server exited: " + log_path.read_text()[-4000:]
                                            )
                                        try:
                                            response = await probe.get(url + "/benchmark/status")
                                            if response.status_code == 200:
                                                break
                                        except httpx.HTTPError:
                                            pass
                                        await asyncio.sleep(0.25)
                                    else:
                                        raise RuntimeError("Benchmark server did not become ready")
                                print(f"Running {mode}, {rate} requests/s, repetition {repeat + 1}", flush=True)
                                result = await traffic(url, rate, args.duration, 100 + repeat)
                                result.update(mode=mode, repetition=repeat + 1)
                                results["runs"].append(result)
                                args.output.write_text(json.dumps(results, indent=2) + "\n")
                                print(json.dumps(result["summary"]), flush=True)
                            finally:
                                stop_process(server)
        finally:
            stop_process(redis)
            redis_log.close()
            if container_created:
                subprocess.run(["docker", "stop", container], stdout=subprocess.DEVNULL, check=False)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", action="store_true")
    parser.add_argument("--source", type=Path, default=ROOT)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--baseline-sha", default="unspecified")
    parser.add_argument("--mode", choices=MODES, default="inline")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--fixture", type=Path, default=ROOT / "app/tests/fixtures/bundles/9692136744.json")
    parser.add_argument("--rates", nargs="+", type=int, default=[5, 10])
    parser.add_argument("--duration", type=int, default=30)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/reference/mixed-traffic-2026-11-01.json")
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    if args.server:
        import uvicorn

        app = make_app(args)
        uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False, log_level="critical", loop="asyncio")
    else:
        if args.baseline is None or args.duration < 1 or args.repeats < 1 or min(args.rates) < 1:
            parser.error("A baseline checkout and positive duration/repeats/rates are required")
        asyncio.run(run(args))


if __name__ == "__main__":
    main()
