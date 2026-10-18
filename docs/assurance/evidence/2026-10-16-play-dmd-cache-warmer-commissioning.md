# Play dm+d Cache Warmer Commissioning Evidence (16 October 2026)

## 1. Evidence Metadata

- **Environment:** Play target environment
- **Component:** dm+d Cache Warmer (Azure Container Apps Job) & Target-Local Redis DB 2
- **Date:** 16 October 2026
- **Execution Mode:** Manual Job trigger (scheduler disabled)
- **Classification:** Non-production commissioning evidence (No PHI, no secrets, no protected infrastructure identifiers)

---

## 2. Scope and Objective

This document provides a point-in-time record of the initial commissioning and verification of the dm+d cache warmer in the Play target environment. It documents the preflight checks, execution metrics, cache state verification, and live application-path lookup behaviour observed following the warm execution.

---

## 3. Warmer Execution Metrics & Verification Results

The warmer Container Apps Job executed with the digest-pinned application image and completed without error:

| Metric / Check | Observed Value | Verification Status |
| :--- | :--- | :--- |
| **Discovered dm+d Release** | `202610.1.0` | **Verified** |
| **ROUTE Concepts** | 79 | **Verified** |
| **UOM Concepts** | 190 | **Verified** |
| **VMP Concepts** | 24,684 | **Verified** |
| **AMP Concepts** | 167,499 | **Verified** |
| **Total Populated Concepts** | 192,452 | **Verified** |
| **Warm Execution Duration** | 172.07 seconds (~2.87 min) | **Verified** (Well within 1800s replica timeout) |
| **Redis Memory Delta** | 80,589,584 bytes (~76.85 MB) | **Verified** (Within 80% memory ceiling) |
| **Redis Eviction Delta** | 0 evictions | **Verified** (Zero-eviction safety invariant satisfied) |
| **Active Release Pointer** | `dmd:active-release` -> `202610.1.0` | **Verified** (Updated only post-verification) |
| **Release Keys TTL** | 9-day TTL configured and active | **Verified** (Grace window maintained) |
| **Distributed Lock State** | `dmd:warm:v1:lock` released | **Verified** (Released cleanly via Lua script) |
| **Direct Cache Read Probe** | `dmd:resolved:v1:202610.1.0:<concept_id>` | **Verified** (Low-millisecond Redis read confirmed) |

---

## 4. Live Application-Path Verification

Following the manual warm execution, an end-to-end ITI-38 document query transaction was exercised to verify live application cache consumption:

1. **Document Cache Miss:** A document cache miss was observed (`xhuma.document.cache.read` returned miss), triggering GP Connect structured record retrieval and C-CDA translation.
2. **Resolved Terminology Cache Hit:** During medication conversion, the application queried the resolved terminology cache (`xhuma.terminology.resolved.cache.read`), resulting in an immediate cache hit against active release `202610.1.0`.
3. **No Terminology HTTP Dependency:** Telemetry confirmed that no outbound HTTP call to the NHS Terminology Server (`xhuma.terminology.http`) was made for the cached concept lookup, proving that target-local Redis DB 2 served the required clinical concept directly.

---

## 5. What This Evidence Proves and What It Does Not

To maintain evidence-based operational assurance, boundaries must be stated explicitly:

### What This Evidence Proves:
- **Warmer Infrastructure & Connectivity:** Demonstrates that the target-local Container Apps Job in Play successfully authenticates against the shared Key Vault via managed identity, discovers the upstream NHS Terminology Server, downloads dm+d release `202610.1.0`, populates target-local Redis DB 2, and validates concept counts.
- **Safety Invariant Adherence:** Confirms that memory preflight, zero eviction delta, atomic active release pointer update, and clean distributed lock release behaved as engineered.
- **Application Cache Integration:** Proves that the runtime FastAPI application detects the active release pointer and serves resolved medication concepts from Redis DB 2 without falling back to remote HTTP queries.

### What This Evidence Does NOT Prove:
- **Not Complete Medication Coverage:** Does not prove that every possible NHS medication concept is cached or that all future prescription scenarios will achieve a cache hit.
- **Not Clinical Mapping Correctness:** Does not prove clinical transformation semantics or safety boundaries (governed separately by [dm+d Mapping](../../clinical-mapping/dm+d_mapping.md) and clinical safety case).
- **Not Weekly Freshness Automation:** Does not prove that the warmer will automatically advance to a newly published release on future Mondays; automated scheduler recurrence remains disabled pending accepted freshness watchdog monitoring.
- **Not INT or PRD Commissioning:** Does not establish that INT or PRD are warmed or commissioned; warmer infrastructure remains to be explicitly enabled and commissioned for INT and PRD in a separate promotion step.
