# ADR 0003: Shared-Nothing Terraform State Boundary

## Status
Accepted (Implemented: June 2026)

## Context
As Xhuma expands to serve multiple distinct NHS Trusts, we must decide how to partition the cloud infrastructure. Multi-tenancy in healthcare middleware carries extreme risks of cross-contamination, configuration drift, and blast-radius escalation if a single tenant is compromised. 

## Decision
We implemented a strict "Shared-Nothing" Terraform state boundary for each Trust environment.
1. **Isolated State Files**: Each Trust deployment (e.g., UCLH, GSTT) has its own independent Terraform state file.
2. **Dedicated Resource Groups**: A Trust deployment provisions its own dedicated Azure Resource Group containing an isolated App Service, VNet, Managed Redis, Key Vault, and Application Insights workspace.
3. **No Shared Networks**: VNet peering or shared subnets between Trust environments are strictly prohibited.

## Consequences

### Positive
- **Blast Radius Containment**: A configuration error, infrastructure failure, or security breach in one Trust's environment cannot compromise or take down another Trust's deployment.
- **Data Sovereignty/Isolation**: Cryptographic keys (Key Vaults) and transient caches (Redis) are physically isolated per Trust, satisfying strict Information Governance (IG) requirements.
- **Independent Lifecycles**: Upgrades or infrastructure changes can be rolled out to Trusts progressively rather than simultaneously.

### Negative
- **Operational Overhead**: Deploying a change across all Trusts requires running the Terraform pipeline multiple times against different state files.
- **Cost**: Provisioning dedicated infrastructure per Trust (rather than sharing a larger App Service Plan) increases Azure compute costs.
