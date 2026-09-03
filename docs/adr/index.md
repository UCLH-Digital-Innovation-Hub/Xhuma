# Architecture Decision Records (ADR) Index

This directory contains records of significant architectural decisions made for the Xhuma project. 

> **Security Note:** These public records document the rationale and context for architectural choices. For security and information governance compliance, specific configurations, tenant IDs, cryptographic keys, issuer CNs, and internal environment endpoints are redacted from this repository and are maintained in the private Trust document store.

## Current ADRs

| Number | Title | Decision | Status |
|--------|-------|----------|--------|
| [0001](0001-hscn-relay-architecture.md) | HSCN Relay Architecture | Adopt an asynchronous, outbound WebSocket-based Relay Client to securely bridge the HSCN network gap. | Accepted (Implemented: Mid-June 2026) |
| [0002](0002-azure-migration.md) | Azure Migration and Centralized Telemetry | Migrate core deployment to Azure App Service and centralize telemetry via Azure Application Insights. | Accepted (Implemented: June 2026) |
| [0003](0003-shared-nothing-terraform-state.md) | Shared-Nothing Terraform State Boundary | Enforce a strict shared-nothing Terraform state and infrastructure boundary per Trust to guarantee isolation. | Accepted (Implemented: June 2026) |
| [0004](0004-jwks-blob-migration.md) | Migrating JWKS to Azure Blob Storage | Host the public JWKS on a central Azure Blob Storage container to decouple identity resolution from individual application deployments. | Accepted (Implemented: Late June 2026) |
| [0005](0005-fail-closed-saml-validation.md) | Fail-Closed SAML Validation | Cryptographically validate inbound SAML assertions and strictly reject unauthorized clinical identities. | Accepted (Implemented: August 2026) |
| [0006](0006-api-fuzzing-resilience.md) | API Fuzzing Resilience and Pentest Remediation | Implement strict FastAPI constraints and opaque exception handlers to prevent fuzzing DoS and information disclosure. | Accepted (Implemented: August 2026) |
| [0007](0007-use-pydantic-for-fhir-inbound-validation.md) | Pydantic V2 for Inbound FHIR Validation | Use Pydantic V2 strictly at the inbound application boundary to enforce schema validation. | Rejected / On Hold (September 2026) |
| [0008](0008-multi-trust-matrix-deployment.md) | Multi-Trust Matrix Deployment Strategy | Adopt a matrix deployment strategy to scale across multiple NHS Trusts using isolated infrastructure and a centralized identity hub. | Proposed / Accepted (September 2026) |
