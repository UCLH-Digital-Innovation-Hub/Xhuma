# dm+d Mapping overview

## Overview

Structured medication prescription data from GP systems is limited in that GP's prescribe fixed quantities of a specific medicinal product. This results in almost all structured doses being in the form of a integer quantity of tablet or capsule.

Example fhir dosage snippet:
```json
"dosageInstruction": [
                    {
                        "text": "1 tablet, daily",
                        "timing": {
                            "repeat": {
                                "frequency": 1,
                                "period": 1,
                                "periodUnit": "d"
                            }
                        },
                        "doseQuantity": {
                            "value": 1,
                            "unit": "tablet",
                            "system": "http://snomed.info/sct",
                            "code": "428673006"
                        }
                    }
                ],
```

As we can see from this typical example the dose quantity is for a single tablet rather then for a dose of drug in units such as mg and so will not map naturally to secondary care prescribing systems. We also do not have a route but this is typically "take" for prescriptions originating in GP systems and again does not map.

## Options

### REGEX
One potential solution would be to use regular expressions to identify the dose within the text of the drug name. For many commonly prescribed medications this would be relatively trivial e.g. the following coded medication could trivially be parsed to 15mg
 ```json
 "code": {
                    "coding": [
                        {
                            "system": "https://fhir.hl7.org.uk/Id/emis-drug-codes",
                            "code": "LAOR14898NEMIS",
                            "display": "Lansoprazole 15mg orodispersible tablets",
                            "userSelected": true
                        },
                        {
                            "system": "http://snomed.info/sct",
                            "code": "4053411000001103",
                            "display": "Lansoprazole 15mg orodispersible tablets"
                        }
                    ]
                }
 ```
However at the scale of the entire UK drug catalogue and taking into account the various medications that are mixtures then any expression would rapidly become unwieldy and introduce significant risk into the process

### dm+d
The Dictionary of Medicines and Devices is an assured dictionary or medicines and devices that are used within the NHS. It allows for mapping of drugs from virtual medicinal products to their base ingredients as well as provides additional information such as their appropriate chapter in the BNF, controlled drug status and other information such as tariff information.

Importantly dm+d also includes the specific quantity of drug and unit in an assured way as well of a list of approved routes.

There are two ways of consuming dm+d. Data is released weekly via the TRUD service which is free to sign up to, [Mark Wardle](https://github.com/wardle/dmd) has written an excellent microservice that simplifies integration. Additionally the [NHS Terminology Server](https://digital.nhs.uk/services/terminology-server) is a platinum level service that allows for real time queries of dm+d as well as SNOMED.

Xhuma uses the Terminology server for dm+d queries

### Basic flow

```mermaid
flowchart TD
    id4(SNOMED code in FHiR medication) --> id1(dm+d API Lookup)
    id1(dm+d API Lookup) --> A@{ shape: diamond, label: "Ingredient?"}
    A@{ shape: diamond, label: "Ingredient?"} -- yes --> id2(Map Ingredient quantity)
    id1(dm+d API Lookup)--> B@{ shape: diamond, label: "Route?"}
    B@{ shape: diamond, label: "Route?"} --yes --> id3(Map Route)

```

### Risks and mitigations

Mapping doses still presents a number of risks. Many medications have several ingredients and a number of routes so simple mapping flows risk introducing errors. Additionally mapping and calculation of doses is only desired for medications that are prescribed in the form of "1 tablet"

| Risk/Problem          | Mitigation |
| --------------------- | ----------- |
| Drug with multiple ingredients      | Only map doses with single ingredients       |
| Drug with multiple routes   | Only map routes with single approved route        |
| Drug prescribed as AMP rather than VMP   | Check if AMP, is so then map parent        |
| Only medications with unitless quantity to be mapped   | Only map doses if unit "tablet", "capsule" or None       |
| More then one dosage instruction  | Stop if len(dosage) == >1      |



### Final Flow

```mermaid
flowchart TD
    A1("get_dmd_concept(medicationSNOMED)") --> D@{shape: diamond, label: "property of parent?"}
    D@{shape: diamond, label: "property of parent?"} --YES-->A2(AMP -> lookup VMP parent)
    A2 ---> A3(Parse DMD)
    D@{shape: diamond, label: "property of parent?"} -- No -->A3(Parse DMD)
    A3 --> H@{shape: diamond, label: "Multiple dosage?"}
    H@{shape: diamond, label: "Multiple dosage?"} -- NO --> E@{shape: diamond, label: "single VPI?"}
    H@{shape: diamond, label: "Multiple dosage?"} -- YES --> G@{ shape:  framed-circle, label: "Stop" }
    A3 --> F@{shape: diamond, label: "single Route?"}
    subgraph Ingredient
    E@{shape: diamond, label: "single VPI?"} --Yes--> A4(Parse VPI)
    A4 <--> A5("get_dmd_concept(STRNT_NMRTR_UOMCD)")
    A4 --> A6(Dose and Unit)
    end
    A6 --> A7(dm+d Pydantic model)
    subgraph Route
    F@{shape: diamond, label: "single Route?"} --Yes--> B4(Parse Route)

    B4 --> B6(Route codable concept)
    B4 <--> B5("get_dmd_concept(ROUTECD)")
    end
    E@{shape: diamond, label: "single VPI?"} -- No -->  G@{ shape:  framed-circle, label: "Stop" }
    F@{shape: diamond, label: "single Route?"} -- No -->  G@{ shape: dbl-circ, label:"Stop"}
    B6 --> A7

```

### Integration into C-CDA Medicines section
With a valid pydantic model it is then possible to use this to enhance the medicines data sent as part of the C-CDA.

```mermaid
flowchart TD
Z[fhir medicationStatement]
A@{shape: diamond, label: doseQuantity?}
B@{shape: diamond, label: "unit == tablet, capsule or blank?"}
C@{shape: diamond, label: "len(dosage) == 1"}
D("dmd_lookup(SNOMED_CODE)")
E@{shape:diamond, label: "route == take?"}

Z --> A
A -- Yes --> B -- Yes --> D
D --> C
D --> E

subgraph Dose
C -- Yes --> G(dose = VPI quantity * tablet/capsule number)
C -- No --> K(warn user of multiple dose instructions)
G --> H(remove trailing zeros)
H --> I(map VPI unit)
I --> J(add warning to alert user to automated mapping)
end

subgraph Route
E -- Yes --> F(Replace substance administration routeCode with coded route from dm+d)
end

```

The workflow is robust to edge cases whilst allowing for more informative mapping for the majority of oral medications that otherwise are difficult to easily consolidate into secondary care systems.

---

## dm+d Cache Architecture & Lifecycle

### Publication & Warm Schedule
- **NHS dm+d Publication**: Weekly on Monday at 04:00 UK time via TRUD / NHS Terminology Server.
- **Xhuma Target Pre-warm**: Weekly on Monday at 05:00 UK local time (DST-aware across GMT/BST), with optional per-target minute offset (`dmd_cache_warm_minute`) to prevent simultaneous upstream thundering herds across trusts.

> [!NOTE]
> Terminology freshness is not guaranteed to be instantaneous; upstream publishing schedules or network delays can occur. Xhuma is architected to safely fall back to runtime on-demand resolution or retain the prior active release within its grace window.

### Architecture Flow
```mermaid
flowchart TD
    LA[Azure Consumption Logic App\nRecurrence: Mon 05:xx UK local] -->|Managed Identity\nARM start action 200/202| CAJ[Manual Container Apps Job\n<app_name>-dmd-warm]
    CAJ -->|Digest-pinned image\nvar.dmd_cache_job_image| PY["/code/.venv/bin/python\n-m app.ops.dmd_cache warm"]
    PY -->|1. Acquire lock| LCK[(dmd:warm:v1:lock\nTTL 45m)]
    PY -->|2. Query Terminology Server| TS[NHS Terminology Server]
    TS -->|ValueSet expansion metadata| REL[Discovered Release ID]
    PY -->|3. Preflight memory check| MEM[Redis memory preflight]
    PY -->|4. Page ROUTE & UOM| MAP[Build Route & UOM Maps]
    PY -->|5. Page VMP & AMP| PIP[Batch Redis Pipeline Writes]
    PIP -->|dmd:resolved:v1:release:id| RDB[(Target-local Redis DB 2\nTTL 9 days)]
    PY -->|6. Verify totals & memory| MAN[(dmd:warm:v1:release:manifest\nTTL 9 days)]
    PY -->|7. Atomic activation| ACT[(dmd:active-release\nTTL 8 days)]
    PY -->|8. Safe Lua lock release| LCK
    LA -->|Poll job execution until terminal| RES[Succeeded / Failed]
```

### Key Principles
1. **Target-Local / Shared-Nothing**: Each target environment has its own Container Apps Job and warms its own target-local Redis DB 2. There is no shared terminology microservice and no public/HTTP control endpoint.
2. **Dedicated Digest-Pinned Job Image**: The Container Apps Job image is managed via `var.dmd_cache_job_image` (`${REGISTRY}/${IMAGE_NAME}@${build digest}`). App Service deployment ownership remains with `azure/webapps-deploy` and ignores image-name drift; only the Container Apps Job image is updated in Terraform (`0 to add, 1 to change, 0 to destroy`).
3. **Guarded Shared Key Vault Network Admission via Terraform Outputs**:
   - Post-apply workflow reads applied Terraform outputs from the initialized target state (`dmd_cache_ca_subnet_id`, `dmd_cache_job_name`, `dmd_cache_job_image`).
   - App Service subnet is expected unconditionally.
   - Container Apps Job subnet (`ca_subnet`) is added to the shared Key Vault allowed subnet set *only* when `dmd_cache_ca_subnet_id` is non-null.
   - Job image verification runs only when `dmd_cache_job_name` is non-null, comparing the live image against the expected immutable GHCR digest.
   - Plan guarding (`guard_shared_plan.py`) rejects subnet removals, unknown subnet additions, and any other shared resource modification.
4. **Logic App Scheduling, Invariants & RBAC**:
   - Time zone: `GMT Standard Time` (Monday 05:xx UK local time, DST-aware).
   - Target-configurable start time: `dmd_cache_schedule_start_time` (local `YYYY-MM-DDTHH:MM:SS` format without Z) emitted via `azurerm_logic_app_trigger_custom`.
   - Invariants: `schedule_enabled => warm_enabled`, start time required when schedule enabled, alert action group required when schedule enabled.
   - Start action handles both HTTP 200 (`body.name`/`body.id`) and HTTP 202 (`Location` header polling).
   - Scoped RBAC actions: `Microsoft.App/jobs/read`, `Microsoft.App/jobs/start/action`, `Microsoft.App/jobs/executions/read`, `Microsoft.App/locations/containerappsjoboperationstatuses/read`, `Microsoft.App/locations/containerappsjoboperationresults/read`, `Microsoft.App/managedEnvironments/read`. No wildcards and no `listSecrets`.
   - Timing invariant: `replica timeout (1800s) < poll timeout (2100s) < lock TTL (2700s)`.
5. **Platform Retries and Distributed Lock Safety**:
   - `replica_retry_limit = 0`: Platform retries are disabled because a new replica process generates a distinct random lock token while the prior token is still held. Application-level retries for transient HTTP errors occur inside the single running job execution.
   - Distributed lock renewal and release use atomic, token-verified Lua scripts.
6. **Real Monotonic HTTP Retry Budget**:
   - Upstream Terminology queries are governed by an overall 120-second monotonic deadline (`time.monotonic() + budget`).
   - Per-attempt connect, read, write, and pool timeouts are strictly clamped to the remaining deadline: `min(default, remaining)`.
   - `Retry-After` headers and exponential backoffs that would cross the deadline abort immediately without sleeping.
   - Upstream 401 Unauthorized responses trigger at most one in-memory OAuth token refresh per client run without touching Redis.
7. **Disjoint Namespaces & Canonical Raw Cache Keys**:
   - Resolved concepts (`dmd:resolved:v1:<release>:<concept_id>`): Only serialized `DMDConcept` instances.
   - Raw lookups (`dmd:raw:v1:<release>:<concept_id>:properties:<canonical_sig>`): Raw FHIR Parameters dictionaries returned by `get_dmd_concept`.
   - Canonical property signature: `properties=None` and `properties=[]` canonicalize to `none`. Non-empty property lists are whitespace-trimmed, deduplicated, and sorted lexicographically (e.g. `ROUTECD,VPI,parent`), preventing property set collisions.
   - Upstream lookups on cache misses are explicitly pinned to the active release; responses with mismatched releases are rejected before caching.
8. **Warm -> Verify -> Activate Invariant**: Data is pre-warmed into versioned keys. The active pointer (`dmd:active-release`) is updated *only* after all categories complete successfully, totals are verified, and zero Redis key evictions occurred.
9. **Natural TTL Retirement (No Purge)**:
   - Resolved concepts (`dmd:resolved:v1:...`): **9 days** TTL
   - Active release pointer (`dmd:active-release`): **8 days** TTL
   - Success manifest (`dmd:warm:v1:...:manifest`): **9 days** TTL
   The active pointer expires before underlying data expires, ensuring a 24-hour grace window for schedule failures while preventing orphaned pointers. Old release data ages out naturally without requiring destructive Redis operations (`DEL`, `KEYS`, `FLUSHDB`). `FLUSHDB` and wildcard `KEYS *` are strictly prohibited to avoid blocking the single-threaded Redis event loop and risking cross-database eviction or data loss.
10. **Redis Memory Safety & Coexistence Overhead**:
    - **Instance-Wide Physical Memory Sharing**: Redis `maxmemory` and eviction policies are **instance-wide across all logical databases (DB 0 application sessions, DB 1 PDS cache, DB 2 dm+d terminology cache)**. Redis DB 2 shares physical memory with DB 0 and DB 1. An eviction in DB 2 could evict active clinical sessions or PDS data.
    - Sizing models correspond exactly to actual writes: raw lookups (ROUTE + UOM), resolved concepts (VMP + AMP), Redis key/dictEntry overhead (~128 bytes per key), allocator fragmentation (1.25x), and old-release coexistence during the warm.
    - Preflight check verifies that current memory + projected delta with safety multiplier (1.5x) does not exceed 80% of `maxmemory`. Both preflight and per-batch guards fail closed immediately if `maxmemory <= 0`.
    - If `evicted_keys` increases during warm, the run aborts immediately and refuses activation.
11. **Sequential Concurrency & Duration Projection**:
    - Terminology expansion requests execute sequentially (concurrency = 1).
    - The capacity model projects total HTTP requests: discovery and sampling requests (8) plus expansion pages (`ceil(ROUTE/1000) + ceil(UOM/1000) + ceil(VMP/1000) + ceil(AMP/1000)`).
    - Projected duration is calculated as `request_count * p95_latency`. The warmer and capacity plan fail closed if projected duration exceeds the 1800s Container Apps Job execution timeout.
12. **Heartbeat Telemetry & Blocked Scheduler**:
    - Upon warm success, the job emits a structured JSON event: `dmd_cache_event {"event": "warm_succeeded", "release": "...", "duration_seconds": ..., "concept_count": ..., "memory_delta_bytes": ..., "evictions": 0}`.
    - Scheduled Query Rules in Azure Monitor strictly cap query lookback windows to 48 hours (`P2D` / 2880 mins). Therefore, a direct 7.5-day heartbeat alert is not safely implementable directly via AzureRM scheduled query alerts.
    - The Logic App scheduler is intentionally blocked by both a Terraform precondition and a check assertion (`condition = !var.dmd_cache_schedule_enabled`) until a 48h-compatible missed-success watchdog is designed and accepted. Both `dmd_cache_warm_enabled` and `dmd_cache_schedule_enabled` remain `false` across all environments.

### Runtime Fallback Behaviour
When `dmd_lookup(concept_id)` executes during prescription conversion:
1. It queries `dmd:active-release`.
2. **Hit with Active Release**: Checks `dmd:resolved:v1:<release>:<concept_id>`. On hit, returns the resolved `DMDConcept` immediately with zero external network calls.
3. **Miss with Active Release**: Resolves the concept on-demand via the Terminology Server pinned to that active release, writes the resolved `DMDConcept` to `dmd:resolved:v1:<release>:<concept_id>`, and returns it.
4. **No Active Release**: Falls back transparently to unversioned on-demand resolution (`snomed:<concept_id>`). A failed Monday warmer never breaks medication processing.

### Operational CLI Commands
The CLI is built directly into the core Xhuma container under `app.ops.dmd_cache`:
```bash
# Execute prewarm and atomic activation
/code/.venv/bin/python -m app.ops.dmd_cache warm

# Dry-run / capacity planning mode (strictly read-only: no lock, no Redis writes)
/code/.venv/bin/python -m app.ops.dmd_cache plan
/code/.venv/bin/python -m app.ops.dmd_cache warm --dry-run

# Output stable machine-readable capacity plan JSON
/code/.venv/bin/python -m app.ops.dmd_cache plan --json

# Inspect operational cache status
/code/.venv/bin/python -m app.ops.dmd_cache status
```
