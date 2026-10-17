# Xhuma Observability and Diagnostics

This document details the operational observability architecture, the shared Azure Monitor Query Pack (`xhuma-operational-diagnostics`), and the operational diagnostic procedures for Xhuma across all target environments (Play, INT, and PRD).

For the step-by-step operational runbook and deployment approval procedures, see the [Operator's Manual](operators_manual.md).

---

## 1. Telemetry Architecture and Foundations

Xhuma's telemetry pipeline is built upon standard Azure Monitor and OpenTelemetry components:

- **OpenTelemetry Instrumentation:** The FastAPI application is instrumented using the OpenTelemetry Python SDK, generating traces, metrics, and structured log events.
- **Application Insights & Log Analytics:** Each target environment (Play, INT, PRD) routes telemetry to its target-local Log Analytics Workspace and Application Insights component, configured via the target's `APPLICATIONINSIGHTS_CONNECTION_STRING`.
- **Structured Traces & Dependencies:** Outbound HTTP requests to upstream NHS APIs (PDS, SDS, GP Connect), Redis cache interactions, and PostgreSQL audit operations are tracked as correlated OpenTelemetry dependencies.
- **Alerting State & Codification:** Alerting is not fully codified across the entire platform. While specific Terraform modules provision targeted alerts and Action Groups (such as for the automated dm+d warmer scheduler when enabled), general proactive alerting, paging policies, and triage routing remain partially operator-configured rather than fully automated platform-wide IaC. Operators must maintain active monitoring during critical maintenance windows.

```mermaid
flowchart TD
    subgraph Target Environments [Target Environments: Play / INT / PRD]
        App[Xhuma FastAPI App Service]
        Job[dm+d Container Apps Job]
        Redis[(Target Redis Cache)]
        PG[(Target PostgreSQL Audit)]
        
        App -->|OpenTelemetry OTLP| TargetLA[Target Log Analytics & App Insights]
        Job -->|Console Logs / System| TargetLA
        Redis -->|Azure Metrics| TargetLA
        PG -->|Azure Metrics| TargetLA
    end

    subgraph Shared Diagnostics [rg-xhuma-shared]
        QP[Azure Monitor Query Pack<br/><b>xhuma-operational-diagnostics</b><br/>(Stores 20 KQL Query Definitions)]
    end

    Operator([On-Call Operator]) -->|Applies Query Definitions to Target Telemetry| TargetLA
    QP -.->|Queries executed in context of| TargetLA
```

---

## 2. Shared Azure Monitor Query Pack

### 2.1 Overview and Scope Boundaries

Operational diagnostics across Play, INT, and PRD are standardised through a shared Azure Monitor Query Pack:

| Property | Value |
| :--- | :--- |
| **Resource Name** | `xhuma-operational-diagnostics` |
| **Resource Group** | `rg-xhuma-shared` |
| **Type** | `microsoft.insights/querypacks` |
| **Scope** | Cross-target diagnostic queries (Play, INT, PRD) |

#### Important Architectural Boundaries:

1. **Query Definitions Only:** An Azure Monitor Query Pack stores **KQL query definitions only**. It does **not** store, aggregate, or retain application telemetry or log data.
2. **No Duplicate Ingestion or Workspaces:** The Query Pack does not create an additional Log Analytics Workspace, nor does it incur duplicate data ingestion charges. Telemetry remains strictly within each target's local Log Analytics Workspace.
3. **Cross-Target Reusability:** The exact same query definitions can be executed across any Xhuma target environment by scoping the Azure Portal Logs query window to the target-specific resource.
4. **RBAC Isolation:** Access to the Query Pack (`microsoft.insights/querypacks/read`) and access to underlying telemetry (`Microsoft.OperationalInsights/workspaces/read` or `Microsoft.Insights/components/read`) are **distinct and separate RBAC concerns**. Having access to the Query Pack alone does **not** grant access to view target telemetry; operators must hold reader permissions on the specific target's resource group or workspace.

### 2.2 IaC and Management State

- **Current Implementation State:** The `xhuma-operational-diagnostics` Query Pack exists as a shared Azure operational resource in `rg-xhuma-shared`. The 20 KQL query definitions have been verified, captured, and exported.
- **Future IaC Hardening:** Full source-controlled IaC management of Query Pack definitions via Terraform is a planned hardening step. At present, Terraform does not actively manage the Query Pack resource. Changes to diagnostic queries must be reviewed and coordinated carefully.

---

## 3. Query Pack Catalogue (20 Standard Queries)

The shared Query Pack provides 20 standardised queries organised into functional diagnostic categories:

### 3.1 Overview
1. **`Xhuma | Overview | Pipeline stage performance`**  
   Aggregates execution latency and invocation counts across primary request pipeline stages (`pds_lookup`, `sds_lookup`, `gp_connect_fetch`, `fhir_to_ccda`, `audit_persist`), providing a high-level latency breakdown.

### 3.2 SOAP / XDS
2. **`Xhuma | SOAP XDS | Request activity`**  
   Monitors inbound SOAP transactions (ITI-55 Discover Patient, ITI-38 Query Documents, ITI-39 Retrieve Document), tracking message IDs, SOAP actions, and HTTP status codes.

### 3.3 GP Connect
3. **`Xhuma | GP Connect | Pipeline stages`**  
   Traces individual steps within GP Connect processing (token generation, practice routing, FHIR bundle retrieval).
4. **`Xhuma | GP Connect | Upstream latency trend`**  
   Plots 95th and 99th percentile response times of the upstream GP Connect APIs over time.
5. **`Xhuma | GP Connect | Upstream status`**  
   Summarises HTTP status codes returned by upstream GP practice endpoints, distinguishing client errors (4xx) from remote server errors (5xx).
6. **`Xhuma | GP Connect | End-to-end operation breakdown`**  
   Measures full end-to-end duration for document generation requests from initial request receipt to final response serialization.

### 3.4 PDS / SDS
7. **`Xhuma | PDS SDS | Stage health`**  
   Monitors response codes and failure rates for Spine Directory Service (SDS) routing queries and Personal Demographics Service (PDS) patient verification calls.

### 3.5 Relay
8. **`Xhuma | Relay | Connection lifecycle`**  
   Tracks reverse relay agent connection state, connection establishment, TLS negotiation, and disconnections.
9. **`Xhuma | Relay | Upstream diagnostics`**  
   Tracks payload forwarding through the internal proxy/relay to upstream endpoints, reporting relay transport status.

### 3.6 dm+d (Dictionary of Medicines and Devices)
10. **`Xhuma | dm+d | Cache hit/miss summary`**  
    Measures Redis cache hit ratio for dm+d concept lookups (VMP, AMP) during medication conversion.
11. **`Xhuma | dm+d | Recent resolved lookups`**  
    Lists recently resolved dm+d drug codes, structured dosage representations, and mapping latency.
12. **`Xhuma | dm+d | Terminology fallback activity`**  
    Identifies requests where local dm+d cache misses forced fallback HTTP queries to the NHS Terminology Server.

### 3.7 Cache
13. **`Xhuma | Cache | Document hits and misses`**  
    Monitors ITI-39 document retrieval cache hit rate vs cache miss/regeneration events.
14. **`Xhuma | Cache | Document lifecycle summary`**  
    Tracks document caching, TTL expirations, and memory allocation across the CCDA cache store.
15. **`Xhuma | Cache | All cache hit rates`**  
    Combined view of hit/miss rates across all functional Redis databases (PDS, SDS, dm+d, CCDA).

### 3.8 Audit
16. **`Xhuma | Audit | Persistence health`**  
    Verifies that clinical audit events are successfully written to target-local PostgreSQL, highlighting any database latency spikes or connection pool exhaustion.

### 3.9 Security / mTLS
17. **`Xhuma | Security | mTLS diagnostics`**  
    Inspects TLS handshake results, client certificate verification failures, and Epic mTLS connection attempts.

### 3.10 Performance
18. **`Xhuma | Performance | Slow pipeline stages`**  
    Surfaces pipeline operations exceeding defined latency thresholds (e.g. stages taking $> 1500\text{ ms}$).

### 3.11 Errors
19. **`Xhuma | Errors | Failed pipeline stages`**  
    Lists requests that aborted or failed during a specific transformation or lookup stage.
20. **`Xhuma | Errors | Application errors and exceptions`**  
    Captures unhandled exceptions, traceback summaries, and internal server error (HTTP 500) logs.

---

## 4. Operational Diagnostic Decision Matrix

When diagnosing live production or integration issues, consult this decision table to select the starting diagnostic query:

| Symptom / Observed Issue | Initial Diagnostic Queries | Secondary Verification |
| :--- | :--- | :--- |
| **GP Connect integration is failing** | 1. `Xhuma \| GP Connect \| Upstream status`<br/>2. `Xhuma \| Relay \| Upstream diagnostics`<br/>3. `Xhuma \| PDS SDS \| Stage health` | Check practice endpoint reachability; verify relay agent connectivity; check PDS NHS number resolution. |
| **ITI-38 (Query Documents) appears slow** | 1. `Xhuma \| GP Connect \| End-to-end operation breakdown`<br/>2. `Xhuma \| Overview \| Pipeline stage performance`<br/>3. `Xhuma \| Performance \| Slow pipeline stages` | Identify whether delay is upstream GP Connect FHIR retrieval or local FHIR-to-CCDA translation. |
| **Medication translation is making remote terminology calls** | 1. `Xhuma \| dm+d \| Cache hit/miss summary`<br/>2. `Xhuma \| dm+d \| Recent resolved lookups`<br/>3. `Xhuma \| dm+d \| Terminology fallback activity` | Check if dm+d Redis DB 2 is populated; verify active release pointer; check warmer execution logs. |
| **Epic SOAP requests are rejected or dropped** | 1. `Xhuma \| Security \| mTLS diagnostics`<br/>2. `Xhuma \| SOAP XDS \| Request activity` | Inspect client certificate validation; verify Epic IP allowlisting; verify SAML issuer and structure. |
| **Document retrieval (ITI-39) returns stale or missing documents** | 1. `Xhuma \| Cache \| Document hits and misses`<br/>2. `Xhuma \| Cache \| Document lifecycle summary` | Check document TTL in Redis; verify document ID format and hash consistency. |
| **Application returns HTTP 500 or fails unexpectedly** | 1. `Xhuma \| Errors \| Application errors and exceptions`<br/>2. `Xhuma \| Errors \| Failed pipeline stages` | Review stack traces; check PostgreSQL audit connectivity (`Xhuma \| Audit \| Persistence health`). |

---

## 5. What Diagnostic Queries Prove (and What They Do Not)

To maintain rigorous, evidence-based operations, operators must avoid overstating query results:

> [!IMPORTANT]
> **Relay Transport vs Upstream Application Health:**  
> A query result showing relay `outcome=ok` proves solely that the internal relay agent successfully transmitted the packet to the remote endpoint. If the remote endpoint returns an HTTP 500 or malformed FHIR payload, that is an **upstream application failure**, not a relay delivery success.

> [!IMPORTANT]
> **Cache Hit vs Clinical Correctness:**  
> A high cache hit rate in `Xhuma | dm+d | Cache hit/miss summary` proves that Redis is serving previously stored concepts. It does **not** prove that the underlying mapping or clinical dosage translation is clinically correct. Clinical semantics are governed by [dm+d Mapping](../clinical-mapping/dm+d_mapping.md).

> [!IMPORTANT]
> **Liveness vs Functional Readiness:**  
> An HTTP 200 on `/health` or a clean execution log proves process and container liveness. It does **not** prove end-to-end functional or clinical readiness (e.g. Spine connectivity, valid mTLS certificates, or populated cache keys).

---

## 6. How to Run Query Pack Queries in Azure Portal

1. Log in to the [Azure Portal](https://portal.azure.com).
2. Navigate to the target environment's resource group:
   - Play: `rg-xhuma-play`
   - INT: `rg-xhuma-int`
   - PRD: `rg-xhuma-prd`
3. Select the target's **Log Analytics Workspace** or **Application Insights** instance.
4. Click **Logs** in the left navigation blade.
5. In the query dialog, click the **Query Packs** dropdown and select:
   ```text
   xhuma-operational-diagnostics
   ```
6. Browse or filter the 20 standard queries, select the required query, configure the desired time range (e.g. `Last 1 hour`), and click **Run**.
