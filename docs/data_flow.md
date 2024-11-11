# Data Flow Documentation

## Core Data Flows

### 1. Patient Demographics Service (PDS) Flow
```mermaid
flowchart TD
    A[Client Request] -->|ITI-47| B[PDS Lookup]
    B -->|NHS Number| C[PDS FHIR API]
    C -->|Patient Demographics| D[Response Validation]
    D -->|Valid Response| E[Cache Demographics]
    E -->|Formatted Response| F[ITI-47 Response]
    D -->|Invalid Response| G[Error Handler]
    G -->|Error Response| F
```

### 2. Structured Record Retrieval Flow
```mermaid
flowchart TD
    A[Client Request] -->|ITI-38| B[Request Validation]
    B -->|Valid Request| C[Cache Check]
    C -->|Cache Miss| D[SDS Lookup]
    D -->|FHIR Endpoints| E[GP Connect Request]
    E -->|FHIR Bundle| F[CCDA Conversion]
    F -->|CCDA Document| G[Cache Storage]
    G -->|Formatted Response| H[ITI-38 Response]
    C -->|Cache Hit| H
```

## Observability Data Flows

### 1. Metrics Collection Flow
```mermaid
flowchart TD
    A[Application Events] -->|Metrics| B[Prometheus Client]
    B -->|Scrape| C[Prometheus Server]
    C -->|Query| D[Grafana]
    D -->|Alert| E[Alert Manager]
    E -->|Notification| F[Alert Channels]
    
    G[System Metrics] -->|Resource Usage| B
    H[Redis Metrics] -->|Cache Stats| B
    I[Request Metrics] -->|Latency/Errors| B
```

### 2. Logging Flow
```mermaid
flowchart TD
    A[Application Logs] -->|JSON Format| B[Logstash]
    B -->|Process| C[Elasticsearch]
    C -->|Query| D[Kibana]
    
    E[System Logs] -->|Structured| B
    F[Access Logs] -->|Parse| B
    G[Error Logs] -->|Enrich| B
```

### 3. Tracing Flow
```mermaid
flowchart TD
    A[Request Start] -->|Generate Trace ID| B[OpenTelemetry]
    B -->|Collect Spans| C[Trace Processing]
    C -->|Store| D[Trace Storage]
    D -->|Query| E[Trace Analysis]
    
    F[Service Calls] -->|Add Spans| B
    G[Database Ops] -->|Add Spans| B
    H[Cache Ops] -->|Add Spans| B
```

## Data Transformations

### 1. PDS Data Transformation
```
Input: NHS Number
↓
PDS FHIR API Call
↓
Raw Patient Demographics
↓
Validation & Formatting
↓
Output: Structured Patient Information
```

### 2. FHIR to CCDA Conversion
```
Input: FHIR Bundle
↓
Parse Bundle Structure
↓
Extract Clinical Entries
↓
Map to CCDA Templates
↓
Generate XML Structure
↓
Output: CCDA Document
```

## Monitoring Data Flows

### 1. Performance Metrics Flow
```
Request Start
↓
Timing Collection
↓
Metric Aggregation
↓
Prometheus Storage
↓
Grafana Visualization
```

### 2. Error Tracking Flow
```
Error Detection
↓
Error Classification
↓
Log Generation
↓
Alert Evaluation
↓
Notification Dispatch
```

## Cache Data Flow

### 1. Cache Operations
```mermaid
flowchart TD
    A[Cache Request] -->|Key Lookup| B{Cache Hit?}
    B -->|Yes| C[Return Cached Data]
    B -->|No| D[Fetch Fresh Data]
    D -->|Store| E[Cache Storage]
    E -->|Return| F[Response]
    
    G[TTL Monitor] -->|Expire| H[Cache Cleanup]
    I[Memory Monitor] -->|Evict| H
```

### 2. Cache Monitoring
```mermaid
flowchart TD
    A[Cache Operations] -->|Stats| B[Metrics Collection]
    B -->|Store| C[Time Series DB]
    C -->|Query| D[Performance Analysis]
    D -->|Alert| E[Cache Optimization]
```

## Security Data Flow

### 1. Authentication Flow
```mermaid
flowchart TD
    A[Request] -->|JWT Token| B[Token Validation]
    B -->|Valid| C[Permission Check]
    C -->|Authorized| D[Process Request]
    B -->|Invalid| E[Auth Error]
    C -->|Unauthorized| E
    E -->|Log| F[Error Tracking]
```

### 2. Audit Trail Flow
```mermaid
flowchart TD
    A[System Event] -->|Generate| B[Audit Record]
    B -->|Store| C[Audit Log]
    C -->|Index| D[Search Engine]
    D -->|Query| E[Audit Reports]
```

## Health Check Data Flow

### 1. System Health
```mermaid
flowchart TD
    A[Health Check] -->|Probe| B{Service Status}
    B -->|Healthy| C[Update Status]
    B -->|Unhealthy| D[Alert]
    D -->|Log| E[Incident Management]
    C -->|Metrics| F[Health Dashboard]
```

### 2. Dependency Health
```mermaid
flowchart TD
    A[Service Check] -->|Test| B{Dependencies}
    B -->|Available| C[Update Status]
    B -->|Unavailable| D[Circuit Breaker]
    D -->|Activate| E[Fallback Mode]
    E -->|Log| F[Recovery Monitor]
