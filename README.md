# Xhuma - GP Connect to CCDA middleware service

[![Python version](https://img.shields.io/badge/Python-%3E%3D3.13-3776AB?logo=python&logoColor=white)](https://github.com/UCLH-Digital-Innovation-Hub/Xhuma/blob/main/pyproject.toml)
[![Coverage status](https://coveralls.io/repos/github/UCLH-Digital-Innovation-Hub/Xhuma/badge.svg?branch=main)](https://coveralls.io/github/UCLH-Digital-Innovation-Hub/Xhuma?branch=main)
[![CI](https://github.com/UCLH-Digital-Innovation-Hub/Xhuma/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/UCLH-Digital-Innovation-Hub/Xhuma/actions/workflows/ci.yml?query=branch%3Amain)
[![CodeQL](https://github.com/UCLH-Digital-Innovation-Hub/Xhuma/actions/workflows/codeql.yml/badge.svg?branch=main)](https://github.com/UCLH-Digital-Innovation-Hub/Xhuma/actions/workflows/codeql.yml?query=branch%3Amain)
[![Deployment](https://github.com/UCLH-Digital-Innovation-Hub/Xhuma/actions/workflows/matrix-deploy.yml/badge.svg?branch=main)](https://github.com/UCLH-Digital-Innovation-Hub/Xhuma/actions/workflows/matrix-deploy.yml?query=branch%3Amain)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Managed with uv](https://img.shields.io/badge/managed%20with-uv-DE5FE9?logo=uv&logoColor=white)](https://docs.astral.sh/uv/)
[![Lint: Ruff](https://img.shields.io/badge/lint-Ruff-D7FF64?logo=ruff&logoColor=000000)](https://docs.astral.sh/ruff/)
[![Imports: Ruff](https://img.shields.io/badge/imports-Ruff-D7FF64?logo=ruff&logoColor=000000)](https://docs.astral.sh/ruff/rules/unsorted-imports/)

## Overview

Xhuma converts GP Connect structured records into CCDA (Consolidated Clinical Document Architecture) documents. It implements IHE ITI profiles through a FastAPI service, with Redis caching and PostgreSQL audit storage.

For deployment and operations, start with the [Operator's Manual](docs/operators_manual.md) or the [deployment quick-start guide](docs/deployment_for_tired_humans.md).

### Contents

- [Technical architecture](#technical-architecture)
- [System flow](#system-flow)
- [Prerequisites](#prerequisites)
- [Development](#development)
- [Configuration](#configuration)
- [Local deployment](#local-deployment)
- [Azure deployment (NHS TRE)](#azure-deployment-nhs-tre)
- [Redis configuration](#redis-configuration)
- [API documentation](#api-documentation)
- [Testing](#testing)
- [Branch strategy](#branch-strategy)
- [Contributing](#contributing)
- [Licence](#licence)

### Key features

- Stateless application instances for scalability
- Redis caching of patient demographics, directory routing details, terminology and CCDA documents
- PostgreSQL audit records for clinical requests
- IHE ITI profile implementation (ITI-55, ITI-38, ITI-39)
- FHIR to CCDA conversion
- JWT authentication for NHS APIs
- SOAP message handling for healthcare interoperability
- Structured medication dose mapping from AMP/VMP prescriptions using dm+d

## Technical architecture

For detailed documentation, see:

- [Technical architecture](docs/technical_architecture.md)
- [Data flow](docs/data_flow.md)
- [Technical resources](docs/technical_resources.md)
- [Structured dose mapping](docs/dm+d_mapping.md)

## System flow

```mermaid
sequenceDiagram
    participant EHR
    box Xhuma
    participant API as FastAPI
    participant Cache as Redis
    end
    box NHS APIs
    participant PDS
    participant SDS
    participant GP as GP Connect
    end

    EHR->>API: ITI-55 request: Discover patient
    API->>Cache: Find cached PDS patient
    Cache-->>API: Patient demographics or cache miss
    opt Patient not cached
        API->>PDS: Get patient demographics
        PDS-->>API: Patient demographics
        API->>Cache: Cache PDS patient
    end
    API-->>EHR: ITI-55 response: Patient discovery response

    EHR->>API: ITI-38 request: Query documents
    API->>Cache: Find cached document
    Cache-->>API: Document ID or cache miss
    opt Document not cached
        API->>Cache: Find cached PDS patient
        Cache-->>API: Patient demographics or cache miss
        opt Patient not cached
            API->>PDS: Get patient demographics
            PDS-->>API: Patient demographics
            API->>Cache: Cache PDS patient
        end
        API->>API: Read patient's GP practice
        API->>Cache: Find cached SDS routing details
        Cache-->>API: GP Connect routing details or cache miss
        opt Routing details not cached
            API->>SDS: Resolve GP Connect endpoint
            SDS-->>API: GP Connect routing details
            API->>Cache: Cache SDS routing details
        end
        API->>GP: Get structured record
        GP-->>API: FHIR bundle
        API->>API: Convert FHIR to CCDA
        API->>Cache: Store CCDA and document ID
    end
    API-->>EHR: ITI-38 response: Document metadata and ID

    EHR->>API: ITI-39: Retrieve document
    API->>Cache: Get CCDA by document ID
    Cache-->>API: Cached CCDA
    API-->>EHR: ITI-39: CCDA document
```

## Prerequisites

- Python 3.13 or later and [uv](https://docs.astral.sh/uv/) for development and testing
- Git for cloning the repository and installing pre-commit hooks
- Docker with Docker Compose for the container deployment below
- NHS API access credentials, a registered signing key and Spine certificates for live NHS calls
- NHS Terminology Server credentials if dm+d terminology lookups are required; see [Terminology services](#terminology-services)

The default tests run directly with uv and do not require Docker or live NHS credentials.

## Development

Clone the repository and install the development dependencies from the lockfile:

```bash
git clone https://github.com/UCLH-Digital-Innovation-Hub/Xhuma.git
cd Xhuma
uv sync --locked --dev
```

Install pre-commit hooks once per checkout:

```bash
uv run --locked pre-commit install
```

The hooks run automatically on `git commit`. To run all hooks manually:

```bash
uv run --locked pre-commit run --all-files
```

The hooks check YAML, trailing whitespace and final newlines, and run Ruff linting, import sorting and formatting. Ruff uses a 120-character line limit and [Black's code style](https://docs.astral.sh/ruff/formatter/).

Ruff checks the whole repository using the version in `uv.lock` and the rules in `pyproject.toml`, matching CI. The hooks apply fixes where possible; review and stage those edits before retrying the commit. To run the same read-only checks as CI:

```bash
uv run --locked ruff check .
uv run --locked ruff format --check .
```

When changing dependencies in `pyproject.toml`, run `uv lock` and commit the updated `uv.lock`. See [Testing](#testing) for local test commands and [Local deployment](#local-deployment) for running the service.

## Configuration

Copy the example configuration for a local deployment:

```bash
cp .env.example .env
```

Replace the placeholder credentials in `.env`. The table summarises the main settings; [.env.example](.env.example) also includes DNS, telemetry and proxy settings. Defaults below refer to the supplied Compose configuration or example where indicated.

| Setting | Purpose | Default or example | Required when |
| --- | --- | --- | --- |
| `ENV` | NHS environment selection | `dev`; use `int` for integration | Always; the current GP Connect client supports `dev` and `int` |
| `API_KEY` | NHS API client ID and application API key | No usable default | Running the service |
| `JWTKEY` | PEM private key registered with the NHS API platform | Development fallback: `keys/test-1.pem` | Making live NHS calls |
| `ORG_CODE` | Originating care organisation's ODS code | Set for the care organisation | Running the service |
| `ORG_ASID` | Xhuma's registered Spine system ASID | Set for the registered system | Running the service |
| `XHUMA_ODS_CODE` | Xhuma's own ODS code | `Z6G1Z` in the example | Resolving the GP Connect JWT issuer |
| `XHUMA_MHS_PARTY_KEY` | Xhuma's registered MHS party key | Discovered through SDS if omitted | Only if configuring it explicitly |
| `REGISTRY_ID` | Stable document registry identifier | Generated UUID if omitted | Set explicitly when a stable registry identity is needed |
| `REDIS_HOST`, `REDIS_PORT`, `REDIS_DB` | Redis connection | `redis`, `6379`, `0` in Compose | Running the service |
| `REDIS_PASSWORD` | Redis authentication | Replace the example value | Using the supplied Compose Redis service |
| `PDS_CACHE_HMAC_SECRET` | Secret for pseudonymous patient cache keys | Falls back to `API_KEY` | Set a dedicated secret for deployed environments |
| `PDS_CACHE_HOURS`, `SDS_CACHE_HOURS`, `CCDA_EXPIRY_HOURS` | Cache lifetimes in hours | `24`, `12`, `4` | Optional overrides |
| `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB` | Audit database connection | `postgres`, `5432`, `xhuma` in Compose | Running the service |
| `POSTGRES_USER`, `POSTGRES_PASSWORD` | Audit database credentials | `postgres`; replace the example password | Running the service |
| `DATABASE_URL` | Complete async PostgreSQL connection URL; overrides `POSTGRES_*` in the application | Otherwise built from `POSTGRES_*` with TLS required | Local non-TLS PostgreSQL; or when supplying a connection URL |
| `GPCON_PORT` | Published application port on the Docker host | `80` | Optional override |
| `DMD_CLIENT_ID`, `DMD_CLIENT_SECRET` | NHS Terminology Server client credentials | No usable default | Live dm+d terminology lookups |

PostgreSQL stores clinical audit records and must be available for clinical requests. Compose runs Alembic migrations before starting the application.

Direct GP Connect calls also require `keys/nhs_certs/client_cert.pem`, `client_key.pem` and `nhs_bundle.pem`. Compose mounts the `keys/` directory read-only. For deployed certificate and secret management, follow the [Operator's Manual](docs/operators_manual.md).

### Terminology services

If medication conversion requires live dm+d lookups, register for an [NHS Terminology Server system-to-system account](https://digital.nhs.uk/services/terminology-server/how-to-access-the-terminology-server/request-a-system-to-system-account/content). The registration page includes the account request form. Configure the supplied client credentials as `DMD_CLIENT_ID` and `DMD_CLIENT_SECRET` in `.env` or your deployment's secret store. These credentials are separate from the NHS API key and are required when the application calls the Terminology Server.

## Local deployment

After configuring `.env`, set a database URL for the supplied local PostgreSQL container, which does not configure TLS. Replace the password below with the same value as `POSTGRES_PASSWORD`; URL-encode any special characters:

```dotenv
ENV=dev
DATABASE_URL=postgresql+asyncpg://postgres:your_postgres_password_here@postgres:5432/xhuma
```

Start the application and its dependencies:

```bash
docker compose up -d --build xhuma
```

With the default `GPCON_PORT=80`, the service is available at [http://localhost](http://localhost). If you set `GPCON_PORT=8000`, use `http://localhost:8000` instead.

To start the full stack, including the bundled Nginx proxy, configure its hostnames, TLS certificates and client CA paths from `.env.example`, and set `GPCON_PORT` to a free port such as `8000` so Nginx can bind host ports 80 and 443:

```bash
docker compose up -d --build
```

Point the public site at `NGINX_PUBLIC_SERVER_NAME` and the relay agent at `NGINX_RELAY_SERVER_NAME`. The separate relay hostname allows Nginx to require its dedicated client CA at the TLS layer. See the [Operator's Manual](docs/operators_manual.md) for deployed TLS and relay configuration.

## Azure deployment (NHS TRE)

Terraform and GitHub Actions deploy Xhuma to the NHS Azure TRE. Target definitions and branch mappings are maintained in [infra/targets.json](infra/targets.json).

### Infrastructure

Target infrastructure includes an Azure App Service for containers, managed Redis, PostgreSQL audit storage, a local Key Vault and Azure Monitor. Shared resources, including the shared Key Vault and public JWKS, have separate Terraform ownership and state.

### CI/CD pipelines and deployment

- The [matrix deployment workflow](.github/workflows/matrix-deploy.yml) deploys `dev` to `play` and `int` to `int`, using target-specific Terraform state, approval gates and immutable container image digests.
- INT uses the matrix deployment path and reuses its existing Terraform state. The [INT cutover record](docs/assurance/evidence/2026-10-06-int-matrix-cutover.md) documents the migration.
- Production on `main` remains on the legacy [infrastructure](.github/workflows/infra.yml) and [application deployment](.github/workflows/cd.yml) workflows.

Use the [Operator's Manual](docs/operators_manual.md) for GitHub environment setup, Azure deployment credentials, Key Vault population, shared infrastructure adoption and deployment verification. The [deployment quick-start guide](docs/deployment_for_tired_humans.md) gives a shorter walkthrough.

### Observability

Azure Monitor OpenTelemetry correlates logs and traces in Application Insights. Terraform configures `APPLICATIONINSIGHTS_CONNECTION_STRING`; managed Redis metrics are available through Azure Monitor.

## Redis configuration

The supplied local Redis configuration uses:

- A 256 MB cache limit with the `volatile-lru` eviction policy
- RDB snapshots and append-only file (AOF) persistence
- Password authentication and connection limits

The application uses connection pooling and automatic retries. Local metrics can be viewed through Prometheus and Grafana when running the full Compose stack. Configure cache lifetimes using the settings in [Configuration](#configuration).

## API documentation

For the default local application port:

- [Swagger UI](http://localhost/docs)
- [ReDoc](http://localhost/redoc)

If you change `GPCON_PORT`, include that port in the URLs. For Azure or the Nginx proxy, use your configured service URL with `/docs` or `/redoc`.

## Testing

Install development dependencies and run tests directly with uv:

```bash
uv sync --locked --dev
uv run --locked pytest
```

Docker is not required. Tests use fixtures and mocks for NHS calls. An optional Redis integration test starts a local `redis-server` if it is installed and skips otherwise.

To run a focused test module:

```bash
uv run --locked pytest app/tests/test_pds.py
```

To run the unit-test coverage command used by CI:

```bash
uv run --locked coverage run -m pytest --ignore=app/tests/test_api_fuzzing.py --ignore=app/tests/test_fuzzing.py
uv run --locked coverage report
```

The default pytest configuration excludes [SCAL tests](app/tests/scal/) and API fuzzing. Live NHS integration tests in the SCAL directory require the relevant NHS environment, credentials, certificates and test data. These need separate setup and are not part of the default local test run.

## Branch strategy

- `main`: Production releases using the legacy deployment path
- `int`: Integration and stabilisation; deploys to the INT target through the matrix workflow
- `dev`: Active development and feature integration; deploys to the play target through the matrix workflow
- `feature/*`: Feature branches opened against `dev`

## Contributing

1. Create a feature branch from `dev`.
2. Implement changes and add or update relevant tests.
3. Run the tests and pre-commit checks above.
4. Create a pull request to `dev`.

## Licence

This project is licensed under the [MIT licence](LICENSE).
