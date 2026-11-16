# Xhuma Documentation

Welcome to the engineering documentation for **Xhuma**, the GP Connect to CCDA middleware service developed by the UCLH Digital Innovation Hub.

---

## 1. Documentation Authority Model

To ensure clarity and avoid reliance on tribal knowledge, all documentation across the Xhuma repository adheres to a strict authority hierarchy:

1. **Implementation Truth:** Source code (`app/`), Terraform definitions (`infra/`), and GitHub Actions workflows (`.github/workflows/`) are the authoritative source of implementation truth.
2. **Current Operational Procedure:** The [Operator's Manual](operations/operators_manual.md) is the authoritative operational runbook governing present and live operational procedures.
3. **Architecture Documents:** Architecture documents ([Technical Architecture](architecture/technical_architecture.md), [Network Architecture](architecture/network_architecture.md), [Data Flow](architecture/data_flow.md), [Commissioning Layers](architecture/commissioning_layers.md), and [Technical Resources](architecture/technical_resources.md)) explain the intended and current system design, topologies, and trust boundaries.
4. **Clinical Mapping Documents:** Clinical mapping documents ([Allergy Mapping](clinical-mapping/allergy_mapping.md), [dm+d Mapping](clinical-mapping/dm+d_mapping.md), [Investigation Grouping](clinical-mapping/investigation_grouping.md), and [Date Handling](clinical-mapping/date_handling.md)) define clinical transformation semantics and safety boundaries.
5. **Assurance Evidence:** Evidence records ([Assurance Evidence](assurance/evidence/)) are immutable, point-in-time artefacts recording historical observations, cutovers, or clean-room rebuilds. They legitimately describe historical architectures, branch strategies, or rehearsal procedures as they existed on the date recorded.
6. **Conflict Resolution:** Where a current operational document conflicts with a historical assurance evidence record, the **current operational document governs present operation**; the evidence document remains valid for the point in time it records.

---

## 2. Quick Navigation by Task and Persona

Find the relevant documentation based on your role and immediate objective:

| Persona / Task | Description | Document |
| :--- | :--- | :--- |
| **I need to deploy Xhuma quickly** | Fast, step-by-step checklist for safe deployment under pressure | [Xhuma Deployment for Tired Humans](operations/deployment_for_tired_humans.md) |
| **I need the full operator runbook** | Authoritative operational manual, approval gates, diagnostics, and failure recovery | [Operator's Manual](operations/operators_manual.md) |
| **I need to diagnose a live issue** | Shared Azure Monitor Query Pack (`xhuma-operational-diagnostics`) and diagnostic matrix | [Observability & Diagnostics](operations/observability.md) |
| **I need performance / benchmark data** | Baseline throughput, latency profiling, and regression test records | [Benchmarking Guide](operations/benchmarking.md) |
| **I need to understand system architecture** | Container runtime, state externalisation, target-isolated resource groups, and dm+d topology | [Technical Architecture](architecture/technical_architecture.md) |
| **I need to understand networking & trust** | VNet subnets, private endpoints, Epic mTLS, and Key Vault network rules | [Network Architecture](architecture/network_architecture.md) |
| **I need to understand data flow** | Request lifecycle across ITI-55, ITI-38, and ITI-39 transactions | [Data Flow](architecture/data_flow.md) |
| **I need to understand commissioning & SAML** | Integration tiers, SAML trust validation, and commissioning requirements | [Commissioning Layers](architecture/commissioning_layers.md) |
| **I need external technical references** | Specifications for IHE ITI, HL7 CCDA, and NHS Digital APIs | [Technical Resources](architecture/technical_resources.md) |
| **I need clinical mapping semantics** | FHIR-to-CCDA rules for allergies, medications, lab tests, and dates | [Clinical Mapping Index](clinical-mapping/) |
| **I need clinical safety & regulatory status** | Medical device regulatory determination under UK MDR 2002 | [Medical Device Statement](assurance/medical_device_statement.md) |
| **I need assurance & historical evidence** | Point-in-time evidence, clean-room rebuilds, and cutover logs | [Assurance Evidence](assurance/evidence/) |

---

## 3. Repository Documentation Structure

The documentation is structured into dedicated operational, architectural, clinical, and assurance directories:

```text
docs/
├── README.md                                  # Landing page and authority model (this document)
│
├── operations/                                # Authoritative runbooks and operational guides
│   ├── deployment_for_tired_humans.md         # Fast, safe deployment checklist for tired operators
│   ├── operators_manual.md                    # Authoritative runbook, approval gates, and troubleshooting
│   ├── observability.md                       # Shared Query Pack catalogue and diagnostic decision matrix
│   └── benchmarking.md                        # Latency, throughput, and performance benchmarking
│
├── architecture/                              # System design, networking, and commissioning
│   ├── technical_architecture.md              # Container runtime, state management, and dm+d topology
│   ├── network_architecture.md                # VNet topology, private endpoints, and egress routing
│   ├── data_flow.md                           # Sequence diagrams for ITI-55, ITI-38, and ITI-39 flows
│   ├── commissioning_layers.md                # Integration tiers, SAML validation, and Epic trust
│   └── technical_resources.md                 # External NHS, IHE, and HL7 specification references
│
├── clinical-mapping/                          # Clinical transformation semantics and rules
│   ├── allergy_mapping.md                     # AllergyIntolerance to CCDA Alerts section mapping
│   ├── dm+d_mapping.md                        # Medication dose syntax and dm+d terminology resolution
│   ├── freetext_mapping.md                    # Canonical free-text dosage mapping and fallback rules
│   ├── investigation_grouping.md              # Diagnostic report and observation grouping semantics
│   └── date_handling.md                       # Precision handling for clinical date and timestamp conversion
│
├── assurance/                                 # Regulatory, safety, and audit evidence
│   ├── medical_device_statement.md            # UK Medical Devices Regulations 2002 regulatory assessment
│   └── evidence/                              # Immutable historical assurance records and logs
│       ├── 2026-09-23-play-matrix-deployment-rehearsal.md
│       ├── 2026-09-25-play-clean-room-rebuild-and-convergence.md
│       ├── 2026-09-29-investigations-assessment.md
│       ├── 2026-09-29-investigations-report-inventory.csv
│       ├── 2026-10-06-int-matrix-cutover.md
│       ├── 2026-10-09-prd-state-reconciliation.md
│       ├── 2026-10-16-play-dmd-cache-warmer-commissioning.md
│       └── investigation-grouping-comparison/
│
├── reference/                                 # Reference query packs, benchmark profiles, and schemas
│   ├── xhuma-operational-diagnostics-querypack.json
│   ├── benchmarking-profile-2026-10-01.json
│   └── investigation-indexed-benchmark-2026-10-01.json
│
└── assets/                                    # Screenshots and architectural diagrams
```

---

## 4. Key Operational and Deployment Principles

- **Unified Deployment Pipeline:** All target environments (Play, INT, PRD) are deployed via the unified GitHub Actions workflow [matrix-deploy.yml](../.github/workflows/matrix-deploy.yml).
- **Branch-to-Target Progression:** Promotion is strictly pull-request based:
  $$\text{feature/*} \longrightarrow \text{dev (Play)} \longrightarrow \text{int (INT)} \longrightarrow \text{main (PRD)}$$
- **Target Isolation:** Every target has dedicated, target-isolated resource groups, PostgreSQL audit storage, Redis cache, and isolated Terraform state defined in [infra/targets.json](../infra/targets.json).
- **Stateless Application Tier:** The FastAPI container tier maintains no local persistent state; all caching is externalised to Redis, clinical audit to PostgreSQL, and cryptographic secrets to Key Vault.
- **Fail-Closed Safety:** Infrastructure plan changes require separate manual approval; unexpected destructive replacements trigger immediate deployment halts.
