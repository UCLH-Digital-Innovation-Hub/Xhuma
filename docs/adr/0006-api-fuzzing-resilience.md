# ADR 0006: API Fuzzing Resilience and Pentest Remediation

## Status
Accepted (Implemented: August 2026)

## Context
A recent third-party penetration test and OWASP vulnerability scan highlighted several critical areas requiring remediation before production deployment. Specifically:
1. **API Fuzzing Susceptibility**: The FastAPI endpoints were vulnerable to unbounded payloads and malformed requests, leading to potential Denial of Service (DoS) conditions when fuzzed.
2. **Dependency Vulnerabilities**: Several Python dependencies were outdated, containing known CVEs flagged by Dependabot.
3. **Information Disclosure**: Default FastAPI error handlers were leaking internal stack traces and framework-specific metadata to external clients during failure conditions.

## Decision
We implemented a stacked set of security PRs (`chore/dependency-upgrades` -> `feature/api-fuzzing-resilience` -> `fix/pentest-findings`) to harden the application boundary.

1. **Strict FastAPI Constraints**: We applied strict constraints on FastAPI route parameters and request bodies. We implemented explicit `max_length`, `regex` patterns, and tightly bounded `Pydantic` schemas for all incoming REST endpoints.
2. **Dependency Upgrades**: Pinned dependencies were systematically bumped to their latest secure versions, and the lockfile was regenerated.
3. **Custom Exception Handlers**: We replaced FastAPI's default `RequestValidationError` and general `Exception` handlers with custom middleware. This ensures that any unhandled exception or parsing failure results in a generic, opaque HTTP 400 or 500 response, stripping all internal stack traces.

## Consequences

### Positive
- The application is now highly resilient against automated fuzzing tools.
- Clean OWASP / Dependabot security posture.
- Prevents potential reconnaissance by malicious actors via error introspection.

### Negative
- Developers must rely heavily on the internal Azure logs (Application Insights/OpenTelemetry) to debug API validation errors, as the HTTP responses are intentionally opaque.
