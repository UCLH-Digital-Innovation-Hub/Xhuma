# Xhuma Commissioning Trust Architecture

This document clarifies the distinct trust and credential layers required for a successful Xhuma integration with Epic and the NHS GP Connect network.

## Trust & Credential Layers

A single end-to-end clinical query traverses several independent trust boundaries. Each boundary requires its own discrete authentication material.

1. **Xhuma Server TLS Certificate**
   - **Role:** Secures inbound HTTPS connections from Epic.
   - **Type:** Standard Web PKI Certificate (e.g. Let's Encrypt).

2. **Epic Client mTLS Certificate**
   - **Role:** Authenticates the specific Epic instance connecting to Xhuma.
   - **Type:** Client certificate mapped to `MTLS_TRUSTED_THUMBPRINTS`.
   - **Verification:** Xhuma confirms the thumbprint against an explicit allowlist.

3. **Epic SAML Assertion (Signing Identity)**
   - **Role:** Provides the clinical user's identity and context (XSPA).
   - **Validation:** Xhuma strictly validates the `Issuer` against `SAML_TRUSTED_ISSUER`.
   - **Note:** In version 0.9.2, SAML trust relies on the authenticated Epic mTLS tunnel + explicit issuer allowlisting. Full cryptographic XML-DSig verification is deferred to 0.9.3.

4. **NHS API Platform API Key & JWT Signing Key (JWKS)**
   - **Role:** Authenticates Xhuma to the NHS API Platform (PDS).
   - **Material:** `NHS_API_KEY` (used for `iss` and `sub`) and a 4096-bit RSA private key (`JWTKEY`).
   - **Mechanism:** Xhuma signs a JWT assertion (exposing a stable `kid` via `/jwk`) and exchanges it for an NHS OAuth Bearer token.

5. **GP Connect Party Key / ASID & NHS Client Certificate**
   - **Role:** Identifies the requesting clinical organisation to the downstream GP practice.
   - **Material:** `ORG_ASID`, `ORG_CODE` (Party Key), and NHS CA-issued client certificates.

6. **Relay Identity**
   - **Role:** Connects Xhuma to the HSCN network if `USE_RELAY=1`.
   - **Material:** Relay mTLS certificate + `RELAY_MTLS_ALLOWED_CERT_SHA256`.

## Commissioning Failure Ladder

When triaging production failures, the sequence of trust boundaries always progresses in this order. A failure at any step halts the transaction.

1. **mTLS (Network/Transport)**: Fails if Epic presents an unknown certificate thumbprint (HTTP 403).
2. **SAML (Identity)**: Fails if the assertion is missing, issuer is unknown/untrusted, or security context is incomplete (HTTP 401).
3. **PDS OAuth (NHS API Auth)**: Fails if `NHS_API_KEY` is wrong, JWT is malformed, or the JWKS signature is invalid (HTTP 502/Internal Error).
4. **PDS Entitlement (API Product)**: Fails if the `NHS_API_KEY` is valid but not onboarded to the requested API product (e.g., HTTP 401/403 "No apiproduct match found").
5. **SDS (Spine Directory)**: Fails if the GP ODS code cannot be resolved.
6. **Relay (Transport)**: Fails if the relay is down or rejects the Xhuma connection.
7. **SSP (Spine Secure Proxy)**: Fails if the SSP routing rules reject the GP interaction.
8. **GP Provider (Endpoint)**: Fails if the GP system is offline or rejects the payload.
9. **ITI-55/38/39 (Application)**: Xhuma maps the GP FHIR response back to the IHE SOAP response format for Epic.
