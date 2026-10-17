# Technical Resources & Verified Inventory

This document provides a verified inventory of standards, protocols, libraries, and tooling implemented in Xhuma, reconciled directly against `pyproject.toml`, `Dockerfile`, GitHub Actions workflows, and active repository code.

---

## 1. NHS Digital Standards & Services Integration

### 1.1 Authentication & Token Signing
- **JWT Implementation:** Outbound NHS API authentication uses signed JSON Web Tokens (RS512 via `cryptography`, `jwcrypto`, and `pyjwt`).
- **Key Management:** Private keys are stored securely in Azure Key Vault; public JWKS keysets are served via `/jwk`.
- **Token Lifetime:** 300-second assertion validity window with unique `jti` claims.

### 1.2 IHE ITI SOAP Transactions
- **Profiles Implemented:**
  - **ITI-55:** Cross Gateway Patient Discovery (patient demographic verification via PDS).
  - **ITI-38:** Cross Gateway Query (clinical document query; fetches and converts GP Connect structured record on cache miss).
  - **ITI-39:** Cross Gateway Retrieve (retrieves cached C-CDA XML documents by `DocumentUniqueId`).
- **Transport & Envelope:** SOAP 1.2 (`application/soap+xml`), WS-Addressing, ebXML RegRep 3.0, IHE XDS.b.
- **Fault Semantics:** Custom `SoapFault` returning HTTP 400 for SOAP Sender faults and HTTP 500 for SOAP Receiver faults.

### 1.3 FHIR Standards by Upstream Service
FHIR versions are service-specific and are not uniform across all integrations:
- **GP Connect Structured Records:** FHIR STU3 (CareConnect profiles: AllergyIntolerance, MedicationStatement, Condition, Observation, Immunization).
- **Personal Demographics Service (PDS) & Spine Directory Service (SDS):** FHIR R4 where implemented.
- **Endpoint Discovery:** Practice endpoints and ASIDs are resolved dynamically via SDS LDAP/FHIR queries or routed through the configured transport (reverse WebSocket relay or direct HTTPS), rather than relying on static or obsolete integration URLs.

### 1.4 Clinical Document Architecture (C-CDA)
- **Standard:** HL7 Consolidated Clinical Document Architecture (C-CDA) Release 2.1.
- **Conversion Engine:** Custom transformation engine (`app/ccda/`) mapping FHIR resources into structured C-CDA sections (Allergies, Medications with dm+d coding, Problems, Results/Investigations, Immunisations).

---

## 2. Verified Technology Stack

### 2.1 Runtime Platform & Core Frameworks
- **Base Container Image:** `python:3.13-slim` (managed via Astral `uv`).
- **Language:** Python 3.13.
- **Web Framework:** FastAPI with Uvicorn (`uvicorn[standard]`).
- **HTTP Client:** HTTPX (async client with connection pooling and trace propagation).
- **XML Processing:** `xmltodict`, `defusedxml` (safe parsing against XML entity expansion vulnerabilities).

### 2.2 Security & Cryptography
- **Cryptographic Operations:** `cryptography` (X.509 certificate loading, CA signature verification, RSA/EC key operations).
- **JWT & JWKS:** `pyjwt`, `jwcrypto` (token generation, key rotation, and JWKS serialization).
- **mTLS Enforcement:** Custom `MTLSMiddleware` inspecting Azure-forwarded `X-ARR-ClientCert` against CA bundle and optional fingerprint allowlists.

### 2.3 Persistence & In-Memory Data Stores
- **Cache Engine:** Azure Cache for Redis (accessed asynchronously via `redis.asyncio` / `redis`).
  - DB 0: Generated C-CDA documents.
  - DB 1: PDS demographic and SDS directory lookup caches.
  - DB 2: Target-local pre-warmed dm+d terminology concepts.
- **Relational Database:** Azure Database for PostgreSQL Flexible Server.
  - Access Layer: `SQLAlchemy[asyncio]`, `asyncpg`, `sqlmodel`, `psycopg[binary]`.
  - Schema Migrations: `alembic` (transactional DDL).

### 2.4 Cloud & Operational Infrastructure
- **Cloud Provider:** Microsoft Azure (target-isolated resource groups with centrally managed shared services).
- **Container Hosting:** Azure App Service for Containers (stateless application tier).
- **Batch Processing:** Azure Container Apps Job (dm+d cache warmer; target-local manual trigger).
- **Secrets & Identity:** Azure Key Vault (user-assigned and system-assigned managed identities).
- **Infrastructure as Code (IaC):** Terraform (`hashicorp/setup-terraform`, version 1.5.7).
- **Container Registry:** GitHub Container Registry (GHCR) with immutable SHA-256 digest referencing.

### 2.5 Observability & Telemetry
- **Telemetry Pipeline:** OpenTelemetry Python SDK integrated with Azure Monitor (`azure-monitor-opentelemetry`).
- **Instrumentation Packages:**
  - `opentelemetry-api`
  - `opentelemetry-sdk`
  - `opentelemetry-instrumentation-fastapi`
  - `opentelemetry-instrumentation-asgi`
  - `opentelemetry-instrumentation-httpx`
  - `opentelemetry-instrumentation-logging`
  - `opentelemetry-instrumentation-redis`
  - `opentelemetry-exporter-otlp-proto-grpc`
- **Analytics Store:** Azure Log Analytics Workspace and Application Insights per target.
- **Shared Query Library:** Azure Monitor Query Pack (`xhuma-operational-diagnostics`, 20 KQL queries).

---

## 3. Engineering, Testing & Quality Tooling

### 3.1 Test Frameworks
- **Primary Test Runner:** `pytest` with `pytest-asyncio` for asynchronous test suites.
- **Coverage & Benchmarking:** `coverage`, `pytest-cov`, `pytest-benchmark`, `pytest-sugar`.
- **API Fuzz Testing:** `schemathesis` (OpenAPI-driven schema validation).
- **Property-Based Testing:** `hypothesis` (data model and conversion resilience).
- **Performance & Load Testing:** `locust`.

### 3.2 Linting, Formatting & Dependency Management
- **Code Quality & Formatting:** `ruff` (fast unified linter and formatter configuring `select = ["E", "F"]`, `extend-select = ["I"]`, and double-quote styling).
- **Unused Dependency Detection:** `creosote`.
- **Git Hooks:** `pre-commit`.
- **Vulnerability Scanning:** Trivy container vulnerability scanner (`aquasecurity/trivy-action`) running against immutable image digests in CI.
- **Dependency Automation:** GitHub Dependabot.

---

## 4. Architectural Boundaries and Non-Included Technologies

To prevent design ambiguity, the following technologies are **not** part of Xhuma's current configuration or runtime:

- **No `python-jose`:** JWT handling is provided strictly by `pyjwt` and `jwcrypto`.
- **No standalone `black`, `flake8`, `mypy`, or `bandit`:** Code formatting and linting are unified under `ruff`.
- **No `testcontainers`:** Redis and PostgreSQL tests run against local managed services or fixtures.
- **No `mkdocs` or `sphinx`:** Technical documentation is maintained as native GitHub Flavored Markdown within the repository.
- **No rate-limiting or security-header middleware:** Rate limiting and public ingress security headers are handled by upstream Azure App Service / front-door network controls rather than custom application middleware.
- **No `git-flow` or `renovate`:** Promotion follows the unified matrix PR workflow (`feature/*` → `dev` → `int` → `main`) managed via Dependabot and GitHub Actions.
