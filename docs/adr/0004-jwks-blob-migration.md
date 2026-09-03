# ADR 0004: Migrating JWKS to Azure Blob Storage

## Status
Accepted (Implemented: Late June 2026)

## Context
Xhuma originally served its JSON Web Key Set (JWKS) for NHS Spine authentication dynamically via the `/jwk` endpoint within the FastAPI application. As we scale to a multi-trust matrix deployment (ADR 0008) utilizing shared-nothing infrastructure (ADR 0003), hosting the public key within a specific Trust's isolated App Service becomes architecturally flawed. If that specific App Service goes down or is rotated, authentication for *all* Trusts using that key would fail.

## Decision
We migrated the Public Key endpoint from the Xhuma application to a central Azure Blob Storage container.
1. **Centralized Hub**: A Shared Resource Group hosts an Azure Storage Account with static website hosting enabled.
2. **Static Asset**: The `jwks.json` is served statically from `/.well-known/jwks.json` on the Blob Storage endpoint.
3. **Application Decoupling**: The `@app.get("/jwk")` route was safely removed from the Xhuma application codebase (`app/main.py`).

## Consequences

### Positive
- **High Availability**: Azure Blob Storage provides 99.99% uptime, removing the Xhuma App Service as a single point of failure for identity resolution.
- **Seamless Multi-Tenancy**: All isolated Trust deployments (which share the same private signing key) can point NHS Digital to the single, centralized Blob Storage URL for validation.
- **Zero-Downtime Rotation**: Keys can be rotated by simply uploading a new JSON file to the Blob container, leveraging NHS API's 1-hour caching without touching application code.

### Negative
- **Manual Key Syncing**: The private `JWTKEY` (in Key Vault) and the public `jwks.json` (in Blob Storage) are now decoupled. Administrators must ensure they are manually synced during key rotation.
