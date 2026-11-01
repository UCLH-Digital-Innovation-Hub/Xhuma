# Benchmarking and pipeline profiling

The load suite runs a validated ITI-55 → ITI-38 → ITI-39 journey. The offline profiler measures FHIR parsing, conversion and serialisation separately. Use both: a repeatedly cached patient can give excellent HTTP timings while exercising almost none of the converter.

## Running the serverless load test

The [GitHub Actions workflow](../../.github/workflows/load-test.yml) runs on pushes to `int` and on manual dispatch. It creates an Azure Container Instance in the target VNet, loads the mTLS certificate from Key Vault through Managed Identity, and runs the self-contained [Locust file](../../tests/load_tests/locustfile.py) against the App Service. It then uploads the HTML report and removes the container.

In **Actions → Serverless Benchmarking → Run workflow**, configure:

| Input | Default | Meaning |
| --- | --- | --- |
| Users | 5 | Concurrent journeys, with 1–3 seconds between journeys |
| Spawn rate | 2 | New users per second |
| Duration | 3m | Run duration |
| Environment | int | Deployment to exercise |
| Patients | 9692136744 | Comma-separated approved test patients that have retrievable documents |

Push-triggered runs now use the same 5-user/2-per-second defaults as manual runs; previously they silently used 50/10. A journey now makes three requests, so its workload is different from the old weighted discovery/query tasks. Compare endpoint statistics and completed journeys, not the old aggregate request rate.

The environment/repository variables `LOCUST_PATIENTS`, `LOCUST_ORGANIZATION_ID`, and `LOCUST_SAML_ISSUER` can configure automated runs. The explicit manual patient input takes precedence over the repository variable. The selected issuer and organisation must match the target environment's test configuration.

Download `benchmark-report-<environment>` from the workflow's artifacts and open `benchmark_report.html`. Failed requests still produce an artifact when Locust generated a report; the job retains a failing exit status. Container setup failures or truncated ACI logs may prevent report extraction and also fail the job.

For an existing local test deployment:

```bash
LOCUST_PATIENTS=9692136744 LOCUST_CERT_FILE=/path/to/client.pem \
  uv run locust -f tests/load_tests/locustfile.py --headless \
  -u 5 -r 2 -t 3m --host https://your-test-deployment --html report.html
```

`LOCUST_CERT_FILE` overrides Key Vault loading; otherwise `KEY_VAULT_URL` and `PEM_SECRET_NAME` select the certificate. Omit both for a local server that does not require mTLS. A configured certificate failure fails worker initialisation. Optional SAML settings are `LOCUST_SUBJECT_ID`, `LOCUST_ORGANIZATION`, `LOCUST_ORGANIZATION_ID`, `LOCUST_HOME_COMMUNITY_ID`, and `LOCUST_SAML_ISSUER`. Their defaults identify a synthetic load-test context, not a real user's identity.

## Fixed load-test behaviour

The old ITI-38 task reused the ITI-55 body and emitted literal `\\r\\n` sequences. Both tasks also used multipart requests where the ITI-55/38 handlers expect plain SOAP, and omitted required SAML attributes. HTTP status alone could mistake a SOAP error for a successful benchmark.

The corrected flow:

1. Sends plain SOAP ITI-55 with a query identifier and complete application-required SAML context. Checks the query status, matched patient and responding community.
2. Sends a distinct ITI-38 stored query for the selected patient. Checks registry success and extracts the advertised document/repository identifiers.
3. Retrieves that document with ITI-39 using real MIME CRLF delimiters and the content type expected by the current handler. Checks registry success, document identity, base64 decoding, C-CDA root and patient identity.

Validators accept plain SOAP responses and SOAP MIME parts, including the service's existing MIME-header format. A failed step aborts the journey and is counted as a failure, including HTTP-200 SOAP errors. These checks validate benchmark behaviour; they do not replace full C-CDA conformance validation.

The Locust report has stable `POST /SOAP/iti55`, `/SOAP/iti38`, and `/SOAP/iti39` rows, plus a synthetic `FLOW Discovery → query → retrieval` row. The FLOW latency includes all three requests and local response validation; its response length is decoded C-CDA bytes. Endpoint latency measures the HTTP exchange, before semantic validation. **Locust's aggregate includes both POST and FLOW events**: use POST rows for HTTP throughput and the FLOW row for journey throughput/latency. An aborted journey produces fewer than three POST events.

Each journey sends a common W3C trace ID with distinct parent span IDs for its requests. The request event context exposes that trace ID for listeners; it is not a metric label or an extra column in the stock HTML report. Trace propagation requires server instrumentation. Request traces are marked sampled, so measure exporter volume before increasing load substantially.

## Server monitoring points

[app/telemetry.py](../../app/telemetry.py) adds instruments under meter/tracer `xhuma.pipeline`:

| Instrument | Dimensions | Interpretation |
| --- | --- | --- |
| `xhuma.stage.duration` histogram, ms | `stage`, `outcome=ok/error/cancelled` | Elapsed wall time of each pipeline stage, including awaited work |
| `xhuma.cache.lookups` counter | `cache=pds/sds/document/retrieval/terminology`, `outcome=hit/miss` | Cache effectiveness; exceptions are timed errors, not misses |
| `xhuma.event_loop.delay` histogram, ms | None | Scheduling delay above a 100 ms sleep; one cancellable probe per application worker |

Stages are:

| Stage | Work measured |
| --- | --- |
| `pds.lookup`, `sds.device`, `sds.endpoint` | GP Connect's awaited discovery steps, including their cache/audit work |
| `pds.cache.read`, `sds.cache.read` | Cache reads, including calls from ITI-55 |
| `gpconnect.http` | Upstream request, with direct/relay transport attached to the span |
| `fhir.prepare` | Worker queue/transfer, JSON decoding and medication requirement discovery |
| `fhir.models`, `fhir.index` | Successful worker-stage durations reported as histograms in the API process |
| `terminology.prepare`, `terminology.resolved.batch` | Prewarmed medication dm+d preparation and batch cache reads |
| `ccda.worker.wait`, `ccda.document` | Queue/transfer/execution time and complete final conversion job |
| `ccda.convert` | Successful synchronous dictionary rendering duration; excludes dm+d I/O |
| `investigations.graph` | Shared graph construction, once when needed |
| `investigations.group` | Per-report grouping |
| `investigations.render` | Post-grouping component/narrative assembly and model dumping; excludes initial report metadata preparation |
| `ccda.serialize`, `ccda.cache.write` | XML/base64 creation and atomic Redis cache write |
| `document.cache.read`, `document.cache.retrieve` | ITI-38 document index lookup and ITI-39 document/association reads |
| `terminology.cache.read`, `terminology.token`, `terminology.http` | Concept cache read, token HTTP call, concept HTTP calls including a 401 retry |
| `audit.persist` | Audit event construction, database work and commit |

API measurements create spans named `xhuma.<stage>`. The process-worker path reports successful `fhir.models`, `fhir.index`, `ccda.convert` and `ccda.serialize` durations as histograms without child spans. Investigation measurements remain available to in-process callers, but worker telemetry providers are not configured/exported. The old `FHIR2CCDA.convert_bundle` API span is replaced by `xhuma.ccda.document`. Counts and sizes (bundle resources, graph edges/reports/observations, grouping issues, base64 size) are span attributes, not histogram labels. New instruments do not attach patient IDs, concept IDs, document IDs, exception messages or payloads. Existing automatic instrumentation and older application logging retain their own behaviour.

Stages nest: do not add every stage histogram together to infer request latency. `outcome` describes whether an exception escaped the measured block, not whether the final SOAP response succeeded; Locust performs that semantic check. Terminology HTTP spans also carry response status codes.

Use the existing Azure Monitor/OTLP configuration to export these measurements. The application currently attempts to register an OTLP meter provider even when Azure Monitor has already configured one; OpenTelemetry uses the first registered provider. Confirm which exporter receives `xhuma.pipeline` instruments in the target deployment. Provider consolidation and the duplicate instrumentation setup in `main.py` remain cleanup work. Local tests verify recording and cancellation, not delivery to Application Insights.

Start dashboards with endpoint/FLOW p50, p95, p99 and failure rate; stage durations by stage; separate document and terminology hit ratios; event-loop delay max/p95; and existing process CPU/RSS, Redis latency/connections and audit database pool/commit metrics. Document-cache hit rate and terminology-cache hit rate answer different questions. High event-loop delay with high CPU points toward conversion; long cache stages with modest CPU point toward blocking Redis work. These are diagnostic signals, not exclusive causes.

## Reprofile: 1 October 2026

The [raw measurement summary](../reference/benchmarking-profile-2026-10-01.json) contains the initial reprofile and a second run after adding monitoring. Both use local Python 3.14.7, one warm-up and seven measured iterations per fixture with garbage collection between iterations. Timing includes JSON decoding, FHIR models, indexing, conversion and XML/base64; it excludes file reads, HTTP, Redis, database audit and real terminology parsing/lookup. Terminology is a minimal deterministic stub and network connections are blocked. Peak memory is a separate `tracemalloc` run, not process RSS. The deployment uses Python 3.13.

| Fixture under `app/tests/fixtures/bundles` | Resources | Earlier baseline total | Initial reprofile total | Final instrumented run total |
| --- | ---: | ---: | ---: | ---: |
| `9690937472.json` | 133 | 140.04 ms | 58.61 ms | 86.95 ms |
| `9692136744.json` | 478 | 451.03 ms | 214.07 ms | 351.81 ms |
| `9692140466.json` | 113 | 92.41 ms | 46.72 ms | 59.56 ms |
| `investigations/9465700088.json` | 452 | 244.17 ms | 201.11 ms | 204.29 ms |
| `investigations/9692136744.json` | 244 | 138.54 ms | 108.90 ms | 123.87 ms |

These are separate-run comparisons, not an isolated A/B test of telemetry overhead or a production speedup guarantee. The final run also had slower FHIR model construction and serialisation, which the offline harness does not instrument; host/runtime variability affects the absolute totals. Medians for individual stages do not necessarily sum to the median total.

The strongest improvement is in conversion itself: the largest mixed fixture's conversion stage fell from **259.08 ms to 48.48–66.92 ms** across the two new runs. Shallow medication copies avoid recursively copying FHIR owner graphs. Shared investigation preparation avoids rebuilding aliases, edges and ownership for each report. SNOMED-first, nonmutating code selection makes resource reuse safe, but is chiefly a correctness improvement rather than a claimed major speedup. The PDS-token and SDS HTTP fixes remove synchronous network waits; this offline experiment cannot quantify their service-level benefit.

The controlled graph-only comparison from the preceding change measured **3.3–5.6× faster grouping**, including one shared build, over 452–3,616 resources. See [investigation scaling evidence and ordering constraints](../clinical-mapping/investigation_grouping.md#measured-effect-of-sharing-graph-preparation). This is not a 3.3–5.6× improvement to complete requests.

The largest mixed fixture now spends roughly **73–74%** of its measured local pipeline time constructing FHIR models and serialising XML/base64. In the initial reprofile those stages took 85.40 and 72.34 ms respectively, versus 48.48 ms in conversion and 0.27 ms in indexing. The cProfile trace likewise puts recursive FHIR model construction and `xmltodict` emission at the top. Reworking the already cheap resource index is not the next broad optimisation.

The largest mixed fixture produces about 829 KiB of base64 and peaks at about 10.46 MiB of traced Python allocations per isolated pipeline. Four concurrent pipelines took 0.91–1.24 seconds, with 455–627 ms maximum gaps in a 5 ms heartbeat. The 452-resource investigation fixture took about 0.86 seconds for four, with an approximately 865 ms heartbeat gap. These synthetic concurrency probes demonstrate remaining event-loop starvation, not production concurrency capacity.

Reproduce with:

```bash
uv run python -m scripts.profile_converter --output /tmp/converter-profile.json
uv run python -m scripts.profile_converter 9692136744.json \
  --repeat 7 --cprofile /tmp/converter.pstats
```

The [profiling script](../../scripts/profile_converter.py) writes aggregate measurements only, including stage medians, allocation peaks and a four-conversion heartbeat probe. Its cProfile option profiles the first selected fixture. It does not exercise the HTTP workflow or export telemetry, and the stub does not model even a warmed Redis lookup's cost.

## Remaining bottlenecks and priorities

1. **Async Redis migration implemented; measure the remaining round trips.** `RedisClient` now uses `redis.asyncio.Redis`; PDS/SDS, terminology, document query/retrieval, startup writes, cache helpers and statistics await Redis operations. Retry delays use `asyncio.sleep`. Document publication retains its three-key atomic pipeline: queue commands inside `async with`, then await `execute()`. Both database pools close on application shutdown and after partial startup failures. PDS token lookup uses one `GET`, avoiding the former `EXISTS`/`GET` expiry race. Byte responses, cache keys, TTLs, database selection and the existing 10-connection limit/30-second socket timeouts are retained. The timings above predate this migration and exclude Redis I/O; they are not measurements of its benefit. Recheck event-loop delay and cache latency under load, including pool saturation now that operations can overlap. Prewarming still leaves Redis round trips and JSON reconstruction; per-conversion memoisation or pipelining known independent keys can reduce these further.
2. **Reduce or isolate CPU-heavy parsing and serialisation.** FHIR construction, graph traversal, Pydantic dumps, XML emission and base64 run on the event loop. `async def` and `asyncio.gather()` do not parallelise these operations. Evaluate compact XML and fewer intermediate copies with content/output equivalence checks; do not remove clinical validation to chase timings. For larger records, benchmark a bounded process pool or dedicated conversion workers, including queuing, serialisation, cancellation and memory costs. Threads may improve responsiveness but do not promise Python CPU throughput under the GIL. More application workers also need a routing design for the current process-local relay hub.
3. **Investigation adjacency indexing implemented; measure its deployment impact.** The graph now prepares normalised associations and traversal indexes once. An ordered work queue visits only report-reachable relationships while preserving discovery, rendering and diagnostic order. The local A/B experiment measured 2.32–13.49× faster shared graph construction plus grouping across 452–3,616 resources; the largest case decreased from 256.69 ms to 19.02 ms. Retained graph allocations increased from about 607 KiB to 1,391 KiB. These figures exclude parsing, rendering, serialisation and external I/O, so they are not request-level speedups. See [indexed traversal measurements and validation](../clinical-mapping/investigation_grouping.md#measured-effect-of-indexed-traversal).
4. **Prevent duplicate work and bound cache misses.** Concurrent misses for the same concept can issue duplicate HTTP requests and token refreshes; concurrent document misses for a patient can repeat the entire conversion. Add in-flight request coalescing keyed by the complete lookup identity, with exception propagation and cleanup. A process-local task map does not coordinate replicas; introduce a bounded distributed lease only if cross-replica duplication warrants it. Keep concurrency limits on remaining upstream lookups. The medication `gather()` is useful for genuine asynchronous misses but currently creates tasks for every item without a bound.
5. **Measure audit/database cost on the real journey.** Several independently committed audit writes sit on each request, including cached paths. The new `audit.persist` measurements expose their contribution. Tune connection pooling and transaction work based on those results; retain the existing fail-closed audit behaviour.
6. **Defer shared terminology HTTP clients behind the above work if hit rates confirm prewarming.** Long-lived `httpx.AsyncClient` instances avoid repeated connection/TLS setup on misses, and may still matter for PDS/SDS/direct GP Connect. They do not accelerate Redis hits. First verify prewarm coverage for property-specific keys and dependent parent/route/unit concepts. Also address the existing terminology error path: the first lookup response is not generally checked with `raise_for_status()` before caching, so a non-401 JSON error can be cached for a week. Keep errors out of the prewarmed cache and measure fallback/refresh behaviour.

For the next deployed experiment, run a warmed repeated-patient scenario to measure retrieval/audit capacity, then a controlled document-cache-miss scenario with prewarmed terminology and representative approved patients. Use cache counters to verify which scenario actually occurred. The load test deliberately does not delete shared cache keys or fabricate patients. Add a concurrent same-patient miss scenario when evaluating coalescing, and use increasing local fixture sizes for CPU scaling. Monitor load-generator CPU too: each retrieval decodes and parses C-CDA, and the ACI generator has one CPU.


### Async Redis usage

The shared clients are scoped to an application worker/event loop. Network operations and `close()` must be awaited; pipeline creation and command queuing remain synchronous:

```python
value = await redis_client.get("key")
async with redis_client.pipeline() as pipe:
    pipe.setex("key", 60, b"value")
    await pipe.execute()
```

Standalone async scripts using these clients should await both `redis_client.close()` and `snomed_client.close()` in cleanup before their event loop exits. Avoid sharing a connected client between separate event loops or threads. This change permits overlapping requests; it does not deduplicate simultaneous cache misses. Request coalescing remains separate work. The `keys()` helper is now asynchronous on the client, but Redis still executes `KEYS` synchronously on the server; use cursor-based scanning if large administrative cache operations become necessary.

## Prewarmed dm+d and worker comparison

Run the offline worker/serializer comparison with:

```bash
uv run --locked python -m scripts.profile_conversion_workers --repeat 5
```

It compares the Python and Rust serializer backends on identical converted data,
then runs four concurrent documents inline, through one worker and through two
workers. Each document includes raw preparation, full model construction,
rendering and serialization. Synthetic dm+d results replace all external I/O;
process startup is warmed separately and excluded. The heartbeat runs every 5 ms.
Do not run other CPU-intensive work alongside the benchmark when assessing
throughput. Worker dispatch/transfer overhead can make one worker slower than
inline processing while still greatly improving API responsiveness.

Correctness coverage includes exact Rust/Python XML comparisons and real spawned
worker execution. An additional implementation-time comparison against the
pre-refactor converter matched encoded output on five representative fixtures,
with both successful synthetic dm+d enrichment and failed enrichment (10 cases),
controlling generated timestamps and UUIDs.

The historical Query Pack snapshot's explicit GP Connect stage filter predates
worker isolation. For current deployments use:

```kusto
dependencies
| where name in ("xhuma.gpconnect.http", "xhuma.fhir.prepare",
                 "xhuma.terminology.prepare", "xhuma.ccda.worker.wait",
                 "xhuma.ccda.document", "xhuma.ccda.cache.write")
| project timestamp, operation_Id, name, duration, success, customDimensions
| order by timestamp desc
```

Updating a saved live Query Pack is a separate deployment action; this repository
change does not publish monitoring configuration to Azure.

### Local implementation measurement — 2026-11-01

[Recorded results](../reference/conversion-worker-benchmark-2026-11-01.json),
Python 3.14.8, five repetitions, the default large mixed fixture:

| Measurement | Python serializer | Rust serializer |
| --- | ---: | ---: |
| Isolated preparation + conversion | 644.72 ms | 438.89 ms |
| XML/base64 stage | 211.07 ms | 37.42 ms |

| Four documents, Rust serializer | Batch elapsed | Median maximum 5 ms heartbeat gap |
| --- | ---: | ---: |
| Inline | 2,011.33 ms | 2,012.09 ms |
| One conversion worker | 2,138.23 ms | 21.96 ms |
| Two conversion workers | 1,328.67 ms | 31.59 ms |

On this host, Rust reduced isolated total time by about 32%; two workers reduced
the four-document batch time by about 34% relative to inline Rust conversion.
One worker was about 6% slower for the batch but substantially more responsive.
These figures include process transfers and exclude process startup, external I/O
and real dm+d cache latency. They are not Azure capacity predictions or a direct
comparison with the older October profile on a different runtime/host load.

Validation: the application regression suite passed 1,183 tests; the subsequently
added document-cancellation regression and focused worker/telemetry checks also
passed. Ten pre-refactor output comparisons passed. The Linux amd64 Docker image
built successfully, and a network-disabled Python 3.13 container smoke test
confirmed the Rust backend and a real spawned conversion.

## Mixed cached retrieval and new conversion — 2026-11-01

The controlled local comparison uses remote `ucl/dev` commit
`91b738989911e9095d044070224a1d83838118d0` with its locked dependencies,
and the working-tree refactor. The latter is measured inline and with one or
two persistent conversion processes. All candidate modes use xmltodict-fast
1.1.0 with its Rust backend; dev uses xmltodict 1.0.4. Each server has one
Uvicorn event loop. Worker modes retain the configured two queued job slots
and 60-second timeout; rejection is included in the failure counts.

### Method

[`scripts/mixed_traffic_benchmark.py`](../../scripts/mixed_traffic_benchmark.py)
starts disposable local Redis and PostgreSQL 15 services and runs each server
sequentially. The load generator is a separate process. Both source versions
use Python 3.14.8 on the same host with four CPUs in the affinity mask. Process
startup, cache population and connection warmup are excluded from measurement.

Traffic is 80% cached SOAP ITI-39 retrieval and 20% fresh conversion, shuffled
in blocks of five with identical seeds across modes. Arrivals follow a fixed
schedule independent of response completion, avoiding a closed-loop generator
that silently reduces load as latency rises. Two 30-second repetitions are
run at each of 5 and 10 total requests/second. Mode order is shuffled per
repetition. All pending requests are drained before stopping the server.

Cached traffic executes the real SOAP router, SAML parsing, Redis retrieval,
response serialization and PostgreSQL audit path. New traffic executes the
real `process_document` stage, including dm+d cache reads, conversion, auditing
and Redis publication, through a benchmark-only HTTP endpoint. Every new
request converts the same repository fixture, `9692136744.json`, regardless
of previously published documents. A fixed 20 ms asynchronous delay stands
in for upstream I/O. Terminology is prewarmed with synthetic resolved dm+d
concepts; external terminology fallbacks are prohibited and counted.

This isolates the conversion/cache interaction. It does not measure the full
ITI-55/ITI-38 journey, live NHS service latency, TLS, production middleware,
multiple API replicas, cold caches or a distribution of patient record sizes.
The dev comparison is local branch code, not the deployed dev service.

Success requires both the HTTP result and response contents to be valid:
cached retrievals must contain SOAP success and the expected document prefix;
new conversions must return a successful document ID. SOAP failures carried
in HTTP 200 therefore count as failures. The raw artifact retains status codes,
success flags, per-request latency, generator dispatch lag and audit counts,
without response payloads. Latency runs from the scheduled arrival until the
full response body is received. Percentiles below pool individual successful
samples across repetitions; failures are reported separately. New-conversion
p99 is exploratory: only 60 or 120 requests are offered per mode/load.

Reproduce after preparing an isolated checkout/archive of the baseline and
installing its locked environment:

```bash
uv sync --project /tmp/xhuma-mixed-dev-91b73898 --python "$(pwd)/.venv/bin/python"
uv run --locked python scripts/mixed_traffic_benchmark.py \
  --baseline /tmp/xhuma-mixed-dev-91b73898 \
  --baseline-sha 91b738989911e9095d044070224a1d83838118d0 \
  --duration 30 --repeats 2 --rates 5 10
```

The harness requires `redis-server`, Docker and a local `postgres:15` image.
It owns and removes its disposable services. Do not target shared stores.

### Results

[Raw request samples](../reference/mixed-traffic-2026-11-01.json) cover 3,600
measured requests. Throughput is successful requests divided by total elapsed
time including drain, pooled over both repetitions. Latencies are milliseconds
and include only successful requests.

**5 requests/s offered (4 cached + 1 new per second)**

| Mode | Cached success/s | Cached p95 / p99 (ms) | Cached failures | New success/s | New p95 / p99 (ms) | New failures |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Dev branch | 3.95 | 767 / 1243 | 0/240 | 0.99 | 1250 / 1958 | 0/60 |
| Refactor inline | 4.00 | 474 / 869 | 0/240 | 1.00 | 1018 / 1346 | 0/60 |
| Refactor: 1 worker | 4.00 | 69 / 78 | 0/240 | 1.00 | 724 / 913 | 0/60 |
| Refactor: 2 workers | 4.00 | 70 / 89 | 0/240 | 1.00 | 714 / 1334 | 0/60 |

**10 requests/s offered (8 cached + 2 new per second)**

| Mode | Cached success/s | Cached p95 / p99 (ms) | Cached failures | New success/s | New p95 / p99 (ms) | New failures |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Dev branch | 3.25 | 34204 / 38011 | 183/480 | 1.10 | 37604 / 43265 | 19/120 |
| Refactor inline | 7.44 | 3658 / 4919 | 0/480 | 1.84 | 3837 / 4984 | 1/120 |
| Refactor: 1 worker | 7.83 | 84 / 113 | 0/480 | 1.75 | 1842 / 1997 | 13/120 |
| Refactor: 2 workers | 7.92 | 75 / 88 | 0/480 | 1.98 | 888 / 1091 | 0/120 |

At 5 requests/s every mode serves the offered traffic without failures, but
workers reduce cached tail latency substantially. At 10 requests/s, two workers
complete all requests with cached p99 of 88 ms and new-conversion p99 of 1.09 s.
The inline refactor reaches 4.92 s cached p99; dev reaches 38.01 s and suffers
substantial failures. Relative to dev at this offered load, two workers deliver
2.28 times the total successful throughput including drain (9.89 versus
4.35 requests/s). This is a measured result at two offered rates, not a maximum
capacity estimate or a claim of linear scaling.

One worker protects cached traffic but rejects 13/120 new conversions with HTTP
503 at the higher rate. Two workers avoid these rejections on this host. Retain
bounded admission; increasing the queue alone would exchange rejections for
longer waiting. Dev's cached failures comprise 165 HTTP-200 responses failing the
SOAP/document success check and 18 HTTP 500 responses; its new failures are
18 HTTP 500 responses and one transport read error. Inline has one
HTTP 500 for new conversion. Error bodies were not retained, so the precise
causes of those application failures are not established by this run.

All trials had zero terminology fallbacks. Each trial's generator dispatch-lag
p99 was at most 5.65 ms. Real audit writes were observed in every trial. The overloaded second dev
trial recorded only 222 retrieval audits for 240 offered cached requests.
Store audits can precede a failed publication, so audit counts alone are not
a success measure. Successful document payload size was 885,028 base64
bytes in every mode. The machine reports an Intel i7-7600U with two physical
cores/four logical CPUs; all processes shared the host. The dev overloaded
trials varied substantially, so use the results as evidence of overload and
isolation benefit rather than precise production tail-latency predictions.

For this workload, two conversion workers are the strongest configuration.
The response-time benefit under mixed load is much larger than the isolated
CPU-time reduction because cached requests no longer queue behind conversion
CPU work. Repeat on deployment-sized resources with representative record
sizes and longer runs before selecting a production worker count or SLO.
