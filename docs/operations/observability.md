# Xhuma Observability and Diagnostics

This document details the operational observability architecture, the shared Azure Monitor Query Pack (`xhuma-operational-diagnostics`), and the operational diagnostic procedures for Xhuma across all target environments (Play, INT, and PRD).

For the step-by-step operational runbook and deployment approval procedures, see the [Operator's Manual](operators_manual.md).  
A sanitised snapshot of the 20 KQL queries is maintained in [Query Pack Reference Snapshot](../reference/xhuma-operational-diagnostics-querypack.json).

---

## 1. Telemetry Architecture and Foundations

Xhuma's telemetry pipeline is built upon standard Azure Monitor and OpenTelemetry components:

- **OpenTelemetry Instrumentation:** The FastAPI application is instrumented using the OpenTelemetry Python SDK, generating traces, metrics, and structured log events.
- **Application Insights & Log Analytics:** Each target environment (Play, INT, PRD) routes telemetry to its target-local Log Analytics Workspace and Application Insights component, configured via the target's `APPLICATIONINSIGHTS_CONNECTION_STRING`.
- **Structured Traces & Dependencies:** Outbound HTTP requests to upstream NHS APIs (PDS, SDS, GP Connect), Redis cache interactions, and PostgreSQL audit operations are tracked as correlated OpenTelemetry dependencies.
- **Telemetry and Alerting Architecture:** Xhuma emits structured telemetry into the configured Azure Monitor / Application Insights environment. Deploying organisations integrate these signals into their established monitoring and incident-management arrangements. In Xhuma 1.0.0, target-local dm+d warmer infrastructure is enabled across Play, INT, and PRD, while automated scheduling is temporarily deferred across all targets (`dmd_cache_schedule_enabled = false`). Initial commissioning and the first Monday terminology refresh are performed manually. The generic Logic App scheduler implementation and its Azure Monitor failure alerts (`la_triggers_failed`, `la_runs_failed`) remain in Terraform and will be enabled in a subsequent operational update once target RBAC and Action Group prerequisites are commissioned.

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

    subgraph Shared Scope [Shared Xhuma Azure Services Scope]
        QP[Azure Monitor Query Pack<br/><b>xhuma-operational-diagnostics</b><br/>(Stores 20 KQL Query Definitions)]
    end

    Operator([Authorised Operator]) -->|Applies Query Definitions to Target Telemetry| TargetLA
    QP -.->|Queries executed in context of| TargetLA
```

---

## 2. Shared Azure Monitor Query Pack

### 2.1 Overview and Scope Boundaries

The `xhuma-operational-diagnostics` Query Pack provides a consistent diagnostic vocabulary across Xhuma deployments, covering pipeline performance, SOAP/XDS, GP Connect, PDS/SDS, relay behaviour, dm+d, cache behaviour, audit persistence, mTLS, performance and application errors. Automated alerting uses Azure Monitor resources, with notification destinations and escalation routes configured through the deploying organisation's operating model.

| Property | Value |
| :--- | :--- |
| **Resource Name** | `xhuma-operational-diagnostics` |
| **Location** | The shared Xhuma Azure services scope |
| **Type** | `Microsoft.OperationalInsights/queryPacks` |
| **Scope** | Cross-target diagnostic queries (Play, INT, PRD) |
| **Reference Export** | [Query Pack Reference Snapshot](../reference/xhuma-operational-diagnostics-querypack.json) |

#### Operational Integration Boundaries:

1. **Query Definitions Only:** The Query Pack stores reusable KQL query definitions. Application telemetry remains in the target environment's Log Analytics Workspace and Application Insights component.
2. **Resource Efficiency:** The Query Pack does not create an additional Log Analytics Workspace or incur duplicate ingestion charges. Telemetry remains strictly within each target's local workspace.
3. **Cross-Target Reusability:** The exact same query definitions can be executed across any Xhuma target environment by scoping the Azure Portal Logs query window to the target-specific resource.
4. **RBAC Isolation:** Access to the Query Pack (`Microsoft.OperationalInsights/queryPacks/read`) and access to underlying telemetry (`Microsoft.OperationalInsights/workspaces/read` or `Microsoft.Insights/components/read`) are **distinct and separate RBAC concerns**. Having access to the Query Pack alone does **not** grant access to view target telemetry; operators must hold reader permissions on the specific target's resource group or workspace.

### 2.2 IaC and Management State

- **Current Implementation State:** The `xhuma-operational-diagnostics` Query Pack exists in the shared Xhuma Azure services scope. The 20 KQL query definitions have been captured and exported as a reference snapshot in `docs/reference/xhuma-operational-diagnostics-querypack.json`.
- **Future IaC Hardening:** Full source-controlled IaC management of Query Pack definitions via Terraform is a planned hardening step. At present, the JSON snapshot is a reference export, not yet the active Terraform source of truth.

---

## 3. Query Pack Catalogue (20 Standard Queries)

The shared Query Pack provides 20 standardised queries organised into functional diagnostic categories. Each description below reflects what the actual saved KQL proves:

### 3.1 Overview
1. **`Xhuma | Overview | Pipeline stage performance`**  
   Aggregates execution latency and invocation counts across primary request pipeline stages (`pds.lookup`, `sds.trace`, `sds.endpoint`, `gpconnect.http`, `fhir.decode`, `fhir.models`, `fhir.index`, `ccda.convert`, `ccda.serialize`, `audit.persist`), providing a high-level latency breakdown.

### 3.2 SOAP / XDS
2. **`Xhuma | SOAP XDS | Request activity`**  
   Counts recent ITI endpoint activity across `/SOAP/iti55`, `/SOAP/iti38`, and `/SOAP/iti39`. It counts recent ITI endpoint activity; it does **not** currently return message IDs, SOAP action, or HTTP status.

### 3.3 GP Connect

GP Connect tracks the current operation with a local `step` variable, such as `ccda_cache.pipeline_execute`. Audit details and diagnostic messages include that step; caught exceptions also include the original `exception_type`. Diagnostic log entries and Application Insights handled-exception messages exclude raw exception messages, patient identifiers, tokens, and upstream payloads. Audit failures still propagate through the fail-closed audit path.

Caught failures are recorded before their failure audit is attempted, so an audit persistence error cannot hide the original operation failure. Unhandled processing errors and audit failures are rethrown with generic messages after recording the operation and original exception type, preventing upstream handlers from logging sensitive exception text.

Non-200 responses from GP Connect retain the upstream HTTP status. For JSON FHIR `OperationOutcome` errors, the response includes `operation_outcome` with every issue's severity, issue code, coding system/version/code/display, details text, diagnostics, and location/expression fields when supplied. The `error` message includes the complete provider codes, displays and diagnostics, including SSP ASID and forwarding-authorisation failures, following the [NHS GP Connect error-handling guidance](https://digital.nhs.uk/services/gp-connect/develop-gp-connect-services/development/error-handling). ITI-38 returns that message in `RegistryError.codeContext` while retaining the XDS `XDSRegistryError` error code. Provider diagnostics are not truncated or copied into audit details, application logs, or Application Insights; non-FHIR and malformed responses retain the generic HTTP-status error.

Step markers cover NHS number validation, PDS lookup and restriction checks, ODS and SDS identity extraction, endpoint resolution, issuer lookup and JWT creation, request construction, certificate and TLS setup, direct HTTP and relay operations, FHIR validation and indexing, CCDA conversion and serialization, and Redis pipeline writes. Existing `measure()` spans remain in place. SDS has separate spans for cache reads, cache decoding, HTTP requests, response decoding, and cache writes; a cache value being present does not prove that the client received confirmation of its write.

3. **`Xhuma | GP Connect | Pipeline stages`**  
   Traces individual steps within GP Connect processing (`gpconnect.http`, `fhir.decode`, `fhir.models`, `fhir.index`, `ccda.convert`).
4. **`Xhuma | GP Connect | Upstream latency trend`**  
   Reports p50, p95, and maximum response times of the upstream GP Connect API over time where implemented. It does **not** report p99.
5. **`Xhuma | GP Connect | Upstream status`**  
   Summarises HTTP status codes returned by upstream GP practice endpoints, distinguishing client errors (4xx) from remote server errors (5xx).
6. **`Xhuma | GP Connect | End-to-end operation breakdown`**  
   Sums and compares instrumented stage durations under a shared operation ID. It represents the sum of instrumented pipeline stages, not necessarily complete wall-clock request latency.

### 3.4 PDS / SDS
7. **`Xhuma | PDS SDS | Stage health`**  
   Reports calls, failure count, and latency for Spine Directory Service (SDS) routing queries and Personal Demographics Service (PDS) patient verification calls. It does **not** project complete response-code distributions.

### 3.5 Relay
8. **`Xhuma | Relay | Connection lifecycle`**  
   Reports structured relay lifecycle events from application and hub logs (connect, disconnect, route registration). It does **not** prove TLS negotiation.
9. **`Xhuma | Relay | Upstream diagnostics`**  
   Tracks payload forwarding through the internal proxy/relay to upstream endpoints, reporting relay transport status (`transport=relay`).

### 3.6 dm+d (Dictionary of Medicines and Devices)
10. **`Xhuma | dm+d | Cache hit/miss summary`**  
    Measures Redis cache hit ratio for dm+d concept lookups (`cache=terminology` and `cache=terminology.resolved`) during medication conversion.
11. **`Xhuma | dm+d | Recent resolved lookups`**  
    Reports timestamp, operation ID, cache-hit property, duration, and custom dimensions for recent resolved dm+d lookups. It does **not** expose drug codes, dose mappings, or clinical terminology payloads.
12. **`Xhuma | dm+d | Terminology fallback activity`**  
    Identifies requests where local dm+d cache misses forced fallback HTTP queries to the NHS Terminology Server (`xhuma.terminology.http`).

### 3.7 Cache
13. **`Xhuma | Cache | Document hits and misses`**  
    Represents ITI-38 document pointer/cache lookup behaviour (`cache=document`). It does **not** represent ITI-39 document retrieval or regeneration.
14. **`Xhuma | Cache | Document lifecycle summary`**  
    Compares document pointer/read (`document.cache.read`) and retrieval cache (`document.cache.retrieve`) instrumentation. It does **not** claim to expose TTL expiry or memory allocation.
15. **`Xhuma | Cache | All cache hit rates`**  
    Covers the instrumented document, retrieval, and terminology cache spans included by its KQL. It does **not** cover PDS or SDS cache spans unless specifically instrumented.

### 3.8 Audit
16. **`Xhuma | Audit | Persistence health`**  
    Reports attempts, failures, and latency for PostgreSQL audit persistence (`xhuma.audit.persist`). It does **not** directly prove connection-pool exhaustion.

### 3.9 Security / mTLS
17. **`Xhuma | Security | mTLS diagnostics`**  
    Reports validation, configuration, and rejection traces logged by the mTLS middleware. It does **not** provide a complete record of every TLS handshake.

### 3.10 Performance
18. **`Xhuma | Performance | Slow pipeline stages`**  
    Surfaces pipeline operations exceeding defined latency thresholds (threshold is **250 ms**, not 1500 ms).

### 3.11 Errors
19. **`Xhuma | Errors | Failed pipeline stages`**  
    Lists pipeline stages that recorded an error or failure outcome (`success == false`).
20. **`Xhuma | Errors | Application errors and exceptions`**  
    Reports error traces, unhandled exceptions, and internal server error logs. It does **not** guarantee complete traceback bodies.

---

## 4. Operational Diagnostic Decision Matrix

When diagnosing live production or integration issues, consult this decision table to select the starting diagnostic query:

| Symptom / Observed Issue | Initial Diagnostic Queries | Secondary Verification |
| :--- | :--- | :--- |
| **GP Connect integration is failing** | 1. `Xhuma \| GP Connect \| Upstream status`<br/>2. `Xhuma \| Relay \| Upstream diagnostics`<br/>3. `Xhuma \| PDS SDS \| Stage health` | Check practice endpoint reachability; verify relay agent connectivity; check PDS NHS number resolution. |
| **ITI-38 (Query Documents) appears slow** | 1. `Xhuma \| GP Connect \| End-to-end operation breakdown`<br/>2. `Xhuma \| Overview \| Pipeline stage performance`<br/>3. `Xhuma \| Performance \| Slow pipeline stages` | Identify whether delay is upstream GP Connect FHIR retrieval or local FHIR-to-CCDA translation. Note: threshold for slow stages is 250 ms. |
| **Medication translation is making remote terminology calls** | 1. `Xhuma \| dm+d \| Cache hit/miss summary`<br/>2. `Xhuma \| dm+d \| Recent resolved lookups`<br/>3. `Xhuma \| dm+d \| Terminology fallback activity` | Check if dm+d Redis DB 2 is populated; verify active release pointer; check warmer execution logs. Lookups query metadata without exposing clinical payloads. |
| **Epic SOAP requests are rejected or dropped** | 1. `Xhuma \| Security \| mTLS diagnostics`<br/>2. `Xhuma \| SOAP XDS \| Request activity` | Inspect client certificate validation traces; verify Epic IP allowlisting; verify SAML issuer and required attributes (`subject_id`, `organization`, `organization_id`, `role`). |
| **Document pointer query (ITI-38) indicates cache misses** | 1. `Xhuma \| Cache \| Document hits and misses`<br/>2. `Xhuma \| Cache \| Document lifecycle summary` | Check document cache read operations; verify patient NHS number hash consistency. (Note: ITI-39 retrieval is tracked under `document.cache.retrieve`). |
| **Application returns HTTP 500 or fails unexpectedly** | 1. `Xhuma \| Errors \| Application errors and exceptions`<br/>2. `Xhuma \| Errors \| Failed pipeline stages` | Review error traces; check PostgreSQL audit persistence latency (`Xhuma \| Audit \| Persistence health`). |

---

## 5. Interpreting Diagnostic Telemetry

To ensure effective operational triage, operators interpret query findings alongside system context:

- **Relay Transport vs Upstream Application:** A query result showing relay `transport=relay` or `outcome=ok` confirms successful transmission across the reverse tunnel. If the upstream GP practice endpoint returns an HTTP 500 or application error, the issue resides in upstream processing rather than relay transport.
- **Cache Hit vs Clinical Transformation:** Telemetry in `Xhuma | dm+d | Cache hit/miss summary` verifies concept retrieval from Redis. Clinical transformation correctness is governed by the mapping rules defined in [dm+d Mapping](../clinical-mapping/dm+d_mapping.md).
- **Diagnostic Queries & Operational Monitoring:** The shared Query Pack provides on-demand diagnostic inspection across telemetry stages. Automated alerting uses Azure Monitor resources, with notification destinations and escalation routing integrated through the deploying organisation's operating model.

---

## 6. How to Run Query Pack Queries in Azure Portal

1. Log in to the [Azure Portal](https://portal.azure.com).
2. Navigate to the target environment's resource group:
   - Play: the Play target resource group (`<play-resource-group>`)
   - INT: the INT target resource group (`<int-resource-group>`)
   - PRD: the PRD target resource group (`<prd-resource-group>`)
3. Select the target's **Log Analytics Workspace** or **Application Insights** instance.
4. Click **Logs** in the left navigation blade.
5. In the query dialog, click the **Query Packs** dropdown and select:
   ```text
   xhuma-operational-diagnostics
   ```
   *(Residing in the shared Xhuma Azure services scope).*
6. Browse or filter the 20 standard queries, select the required query, configure the desired time range (e.g. `Last 1 hour`), and click **Run**.
