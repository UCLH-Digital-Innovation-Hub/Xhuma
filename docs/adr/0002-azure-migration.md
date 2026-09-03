# ADR 0002: Azure Migration and Centralized Telemetry

## Status
Accepted (Implemented: June 2026)

## Context
Xhuma originally utilized local, decentralized logging and deployment models. However, moving toward production scaling and SCAL (Structured Clinical Assurance Layer) conformance required enterprise-grade monitoring, fault-tolerance, and auditable metrics.

## Decision
We migrated the core deployment architecture and telemetry systems to the Azure ecosystem.

1. **Azure Log Stream & Application Insights**: We refactored `app/logging.py` to route all stdout logs directly into Azure Log Stream. We also integrated `opentelemetry` to push application failures directly into the Azure Application Insights "Failures" blade.
2. **Containerization & CI/CD**: The application was containerized, with GitHub Actions orchestrating the deployment of the immutable Docker image into the Azure environment.
3. **Transient Cache Isolation**: Redis was configured strictly as a transient cache with volatile-lru eviction.

## Consequences

### Positive
- **Observability**: Centralized KQL (Kusto Query Language) querying across all instances. Exceptions are mapped seamlessly in Application Insights.
- **SCAL Compliance**: Provides the necessary metric auditing and monitoring layers required by clinical safety officers.
- **Scalability**: The stateless architecture allows Azure to seamlessly horizontally scale the FastAPI container instances based on load.

### Negative
- **Vendor Lock-in**: Hard dependency on Azure-specific logging agents and telemetry paradigms (`opentelemetry` configuration tuned specifically for Application Insights).
- **Local Debugging**: Developers must rely on `sys.stdout` streaming locally, which lacks the advanced UI filtering provided by Application Insights unless simulated locally.
