# ADR 0007: Pydantic V2 for Inbound FHIR Validation

## Status
Rejected / On Hold (September 2026)

## Context
Xhuma acts as a stateless middleware proxy, retrieving raw FHIR STU3 JSON bundles from NHS GP Connect and translating them into C-CDA XML documents for ingestion by Epic Care Everywhere. 

Historically, Xhuma relied on the `fhirclient` library to parse incoming GP Connect JSON bundles. However, `fhirclient` had several critical shortcomings:
1. **Permissiveness**: It blindly parsed JSON into Python objects without enforcing clinical data standards (such as SCAL requirements for SNOMED CT terminology codes).
2. **"Garbage In, Garbage Out"**: Because it didn't enforce clinical data rules at the boundary, downstream C-CDA translation logic (`app/ccda/entries.py`) had to be heavily defensive, masking missing data and risking silent clinical omissions in the final record.
3. **Performance**: It is an older, pure-Python library that struggles with large FHIR bundle payloads.
4. **Maintenance**: The library is no longer actively maintained.

While our outbound C-CDA generation logic was strictly typed using Pydantic V2, our inbound FHIR parsing was weak.

## Decision
We will completely replace the `fhirclient` dependency for core clinical domains (Allergies, Medications, Labs/Observations) with bespoke, strictly-typed Pydantic V2 models. 

1. **Inbound Firewalls**: We will define `app/fhir/allergies.py`, `app/fhir/medications.py`, and `app/fhir/labs.py` using Pydantic V2.
2. **SCAL Enforcement**: We will use Pydantic `@field_validator` hooks to strictly enforce NHS SCAL (Structured Clinical Assurance Layer) rules directly at the parsing boundary (e.g., rejecting Medications or Allergies that lack a valid SNOMED CT system code, while gracefully handling authorized "Transfer-degraded" codes).
3. **Runtime Casts**: We will enforce that the translation endpoints in `app/ccda/entries.py` explicitly cast incoming generic bundle resources through these Pydantic models before executing translation logic, ensuring runtime validation is never bypassed.
4. **Surgical Scope**: We will use `extra="ignore"` to discard FHIR bloat, extracting only the specific fields required by Xhuma's C-CDA mappings.

## Consequences

### Positive
- **Guaranteed Clinical Safety**: The C-CDA translation logic is now protected by a "Fail Fast" mechanism. If GP Connect sends clinically unsafe/uncoded data, it is instantly rejected before translation, preventing malformed records from reaching Epic.
- **Architectural Symmetry**: Both the inbound (FHIR) and outbound (C-CDA) edges of the application now run on the identical, high-performance Pydantic V2 validation engine (powered by Rust).
- **Reduced Complexity**: Defensive coding (`try/except` and `hasattr` checks) in the translation layer can be significantly reduced because Pydantic guarantees the object contract.

### Negative
- **Maintenance Burden**: We now own the FHIR models. If GP Connect heavily updates the STU3 spec, we must update our bespoke models rather than pulling a library update.
- **Transitional State**: Demographic parsing (Patient/Organization) currently still relies on `fhirclient`. Complete removal of the dependency will require mapping those final resources in the future.
