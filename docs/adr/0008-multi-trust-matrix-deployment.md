# ADR 0008: Multi-Trust Matrix Deployment Strategy

## Status
Proposed / Accepted (September 2026)

## Context
Xhuma has achieved a highly defensible security posture (zero high/critical vulnerabilities in CREST-aligned pentesting) and proven its integration model. As demand grows, we need a strategic deployment framework to rapidly onboard multiple NHS Trusts while maintaining strict data isolation, security, and SCAL compliance.

## Decision
We will adopt a "Matrix Deployment Strategy" for multi-trust rollouts. This strategy acts as the synthesis of our foundational architectural decisions:
1. **Shared-Nothing Infrastructure (ADR 0003)**: Every new Trust receives a completely isolated Azure Resource Group (App Service, Redis, Key Vault) deployed via a parameterized Terraform pipeline.
2. **Centralized Identity Hub (ADR 0004)**: All Trust deployments authenticate to the NHS Spine using a unified, highly available JWKS hosted on a centralized Azure Blob Storage account.
3. **Per-Tenant Configuration**: Environment variables (e.g., `SAML_TRUSTED_ISSUER`, `ORG_CODE`) are injected per-tenant, allowing bespoke clinical workflows without altering the core Docker image.

## Consequences

### Positive
- **Rapid Onboarding**: New Trusts can be spun up in minutes via Terraform, requiring only the provision of their unique Epic mTLS certificates and ODS codes.
- **Security Assurance**: The blast radius remains strictly confined to a single Trust. Vulnerability scanning, fuzzing resilience (ADR 0006), and fail-closed SAML (ADR 0005) are inherited by default across the matrix.
- **Centralized Governance**: While infrastructure is decentralized, identity resolution and application image versions remain centrally governed.

### Negative
- **Pipeline Complexity**: CI/CD pipelines must be engineered to orchestrate matrix deployments across multiple state files simultaneously to keep application versions synchronized across the fleet.
