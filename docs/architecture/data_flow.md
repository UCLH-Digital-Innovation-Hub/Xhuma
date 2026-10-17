# Data Flow Documentation

This document describes the actual runtime data flows, transformations, observability pipelines, and security checkpoints implemented in Xhuma.

---

## 1. Core Clinical Data Flows

### 1.1 Patient Demographics Service (PDS) Flow (ITI-55)
```mermaid
flowchart TD
    A[Epic Client Request] -->|ITI-55 Cross Gateway Patient Discovery| B[SOAP & SAML Validation]
    B -->|Validated Context & NHS Number| C{Redis DB 1 Cache Check}
    C -->|Cache Hit| F[Format ITI-55 Response]
    C -->|Cache Miss| D[PDS FHIR Client]
    D -->|Signed JWT OAuth Request| E[NHS PDS API]
    E -->|Verified Demographics| G[Validate & Normalise]
    G -->|Store Demographic Record| H[(Target Redis DB 1)]
    H --> F
    F -->|Demographic Match Response| I[Epic Care Everywhere]
    B -->|Missing/Untrusted SAML| J[SOAP Sender Fault / HTTP 400]
    D -->|PDS Error / Unavailable| K[SOAP Receiver Fault / HTTP 500]
```

### 1.2 Structured Record Query & Retrieval Flow (ITI-38 & ITI-39)
```mermaid
flowchart TD
    subgraph ITI38 [Stage 1: Document Query - ITI-38]
        Req38[Epic ITI-38 Request] --> Val38[Validate SAML & Extract NHS Number]
        Val38 --> CacheCheck{Document Cache Check}
        CacheCheck -->|Cache Hit| MetaOnly[Return Document Metadata & ID]
        CacheCheck -->|Cache Miss| SDS[SDS Lookup: GP Endpoint & ASID]
        SDS --> GPC[GP Connect Client]
        GPC -->|FHIR STU3 Query via Relay/Direct| GP[GP Practice System]
        GP -->|FHIR Structured Bundle| Convert[FHIR to C-CDA Converter]
        Convert --> StoreDoc[(Target Redis DB 0: Cache C-CDA)]
        StoreDoc --> Audit38[(Target PostgreSQL: Audit Generation)]
        Audit38 --> MetaOnly
        MetaOnly --> Resp38[ITI-38 AdhocQueryResponse to Epic]
    end

    subgraph ITI39 [Stage 2: Document Retrieval - ITI-39]
        Req39[Epic ITI-39 Request] --> Val39[Validate SAML & Extract DocumentUniqueId]
        Val39 --> RetrieveFromCache[(Target Redis DB 0: Retrieve by Document ID)]
        RetrieveFromCache -->|Document Found| Format39[Package C-CDA into MTOM/XOP Payload]
        RetrieveFromCache -->|Document Not Found| Err39[IHE XDSDocumentUniqueIdError Fault]
        Format39 --> Audit39[(Target PostgreSQL: Audit Retrieval)]
        Audit39 --> Resp39[Deliver ITI-39 Response to Epic]
    end
```

---

## 2. Telemetry and Observability Data Flows

### 2.1 Metrics and Dependency Flow (Azure Monitor / OpenTelemetry)
```mermaid
flowchart TD
    App[FastAPI Request Pipeline] -->|OpenTelemetry Instrumentation| SDK[OpenTelemetry Python SDK]
    SDK -->|OTLP gRPC Export| AI[Azure Application Insights]
    AI -->|Ingest Telemetry| LAW[Target Log Analytics Workspace]
    
    Job[dm+d Container Apps Job] -->|Console & System Logs| LAW
    Redis[(Target Redis Metrics)] -->|Azure Monitor Metrics| LAW
    PG[(Target PostgreSQL Metrics)] -->|Azure Monitor Metrics| LAW
    
    LAW -->|Analysed via 20 KQL Queries| QP[Shared Query Pack: xhuma-operational-diagnostics]
    QP --> Operator([Authorised Operator])
```

### 2.2 Application Logging and PHI Minimisation Flow
```mermaid
flowchart TD
    LogCall[Application Logging Event] --> Sanitiser[Log Sanitiser / Middleware]
    Sanitiser -->|1. Scrub Sensitive Headers: Authorization, Cookie, API Keys| CleanHeaders[Header Sanitisation]
    Sanitiser -->|2. Mask 10-digit NHS Numbers in URL Paths| RedactURL[URL Masking]
    Sanitiser -->|3. Explicitly Replace Request/Response Bodies| RedactBody["Body: [REDACTED FOR PHI SECURITY]"]
    
    CleanHeaders --> OutStream[Stdout StreamHandler]
    RedactURL --> OutStream
    RedactBody --> OutStream
    
    OutStream -->|Container stdout| LogStream[Azure App Service Log Stream / App Insights]
    LogStream --> LAW[Target Log Analytics Workspace]
```

### 2.3 Distributed Tracing Flow
```mermaid
flowchart TD
    InReq[Incoming Request] --> TraceInit[Extract or Generate W3C traceparent]
    TraceInit --> Spans["Instrumented Spans (xhuma.*)<br/>pds.lookup, sds.trace, gpconnect.http, ccda.convert, ccda.serialize, audit.persist"]
    Spans --> Outcomes["Record Stage Metadata<br/>stage.duration_ms, stage.outcome (ok/error/cancelled)"]
    Outcomes --> ExportOTLP[Export to Application Insights]
    ExportOTLP --> Correlation["Correlated End-to-End Traces<br/>operation_Id linked across calls"]
```

---

## 3. Data Transformations

### 3.1 PDS Data Transformation
```text
Input: 10-digit NHS Number (validated via modulus 11 checksum)
  │
  ▼
NHS PDS FHIR API Query (JSON)
  │
  ▼
Raw Patient FHIR Resource (Name, Address, DOB, Gender, Managing Organization)
  │
  ▼
Extraction & Transformation to IHE ITI-55 XML Schema
  │
  ▼
Output: HL7 v3 Patient Discovery Response Envelope
```

### 3.2 FHIR to C-CDA Conversion
```text
Input: GP Connect FHIR STU3 Structured Record Bundle
  │
  ├─► Allergies & Adverse Reactions (AllergyIntolerance -> C-CDA Section 48765-2)
  ├─► Medications & Prescriptions (MedicationStatement -> C-CDA Section 10160-0; dm+d resolution)
  ├─► Clinical Problems & Conditions (Condition -> C-CDA Section 11450-4)
  ├─► Diagnostic Investigations & Observations (Observation -> C-CDA Section 30954-2; graph grouping)
  └─► Immunisations (Immunization -> C-CDA Section 11369-6)
  │
  ▼
XML DOM Construction & Serialization (C-CDA Release 2.1)
  │
  ▼
Output: Consolidated CDA XML Document
```

---

## 4. Cache and Storage Data Flows

### 4.1 Redis Cache Operations
```mermaid
flowchart TD
    Req[Application Cache Access] --> CheckKey{Key Exists in Redis?}
    CheckKey -->|Yes| Hit[Return Cached Concept / Document]
    CheckKey -->|No| Miss[Fetch Fresh Data from Upstream]
    Miss --> Write[Write Key with Configured TTL]
    Write --> ReturnData[Proceed with Request Processing]
    
    subgraph NaturalLifecycle [dm+d Release Lifecycle]
        Discover[Discover Upstream Release] --> Warm[Populate DB 2 with Versioned Keys]
        Warm --> Verify[Verify Category Counts & Memory Delta]
        Verify --> Activate[Atomically Update dmd:active-release Pointer]
        Activate --> Retire[Old Release Keys Expire Naturally via TTL]
    end
```

---

## 5. Security & Authentication Flow

```mermaid
flowchart TD
    Req[Inbound HTTPS Connection from Epic] --> MTLS{Azure App Service mTLS Check}
    MTLS -->|No Client Cert Present| Block403[HTTP 403 Forbidden]
    MTLS -->|Client Cert Present| Middleware[Xhuma MTLSMiddleware]
    
    Middleware --> CertCheck{Verify Certificate Validity & CA}
    CertCheck -->|Expired or Invalid Signature| Reject403[HTTP 403 Forbidden]
    CertCheck -->|Thumbprint Check if Configured| RejectAllow[HTTP 403 if Not Allowed]
    CertCheck -->|Valid| SOAPExtract[SOAP Envelope & Header Parsing]
    
    SOAPExtract --> SAMLCheck{Validate SAML Assertion}
    SAMLCheck -->|Missing Assertion or Untrusted Issuer| Fault400[SOAP Sender Fault / HTTP 400]
    SAMLCheck -->|Missing subject_id, role, or org| Fault400
    SAMLCheck -->|Context Valid| Handler[Execute ITI Transaction Route]
```

---

## 6. Audit Trail Flow

```mermaid
flowchart TD
    Event[Clinical Transaction Event] --> ExtractAudit[Extract Context: Subject Ref, User ID, Role, Org, Action, Outcome]
    ExtractAudit --> Model[Build AuditEventRow Record]
    Model --> DB[(Target-Local Azure PostgreSQL)]
    DB -->|Committed Transaction| Seq[Monotonic Sequence Number]
    
    subgraph DevViewer [Development Environment Only]
        DevAdmin([Authorised Operator]) --> QueryForm[Dev Audit Form: /_dev/audit]
        QueryForm --> ReadDB[(PostgreSQL)]
        ReadDB --> Display[Metadata-Only Audit Table: No Raw PHI]
    end
```

---

## 7. Process Liveness Architecture

```mermaid
flowchart LR
    Probe([Container Orchestrator / Azure App Service]) -->|HTTP GET /health| Endpoint["/health Endpoint (FastAPI)"]
    Endpoint -->|Returns status: ok| Response["HTTP 200 {status: ok}"]
```

> [!IMPORTANT]
> **Coarse Process Liveness Only:**  
> The `/health` endpoint is unauthenticated and performs a fast, coarse process-liveness check confirming that the Uvicorn worker process is running and accepting HTTP connections. It does **not** execute automated dependency health probes (Spine, Redis, PostgreSQL), does **not** test downstream reachability, and does **not** trip circuit breakers or trigger automated failover routines. Complete functional readiness must be verified through the operational procedures in the [Operator's Manual](../operations/operators_manual.md).
