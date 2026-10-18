# Xhuma Commissioning Trust Architecture

This document clarifies the distinct trust and credential layers required for a successful Xhuma integration with Epic and the NHS GP Connect network.

## Trust & Credential Layers

A single end-to-end clinical query traverses several independent trust boundaries. Each boundary requires its own discrete authentication material.

1. **Xhuma Server TLS Certificate**
   - **Role:** Secures inbound HTTPS connections from Epic.
   - **Type:** Standard Web PKI Certificate (e.g. App Service Managed Certificate or enterprise TLS cert).

2. **Epic Client mTLS Certificate**
   - **Role:** Authenticates the specific Epic instance connecting to Xhuma.
   - **Validation Mechanism:**
     - Presence of the Azure-forwarded client certificate header (`X-ARR-ClientCert`);
     - Certificate validity period verification (`not_valid_before` and `not_valid_after`);
     - Cryptographic signature verification against the configured Epic CA certificate bundle (`EPIC_CA_CERT`);
     - Optional SHA-256 fingerprint allow-list verification when `MTLS_TRUSTED_THUMBPRINTS` is configured.

3. **Epic SAML Assertion (Identity and Context)**
   - **Role:** Provides the clinical user's identity, role, and healthcare purpose context (XSPA).
   - **Current Trust Model:**
     - Inbound transport is authenticated through Epic mTLS.
     - SAML assertion presence and XML structure are strictly validated.
     - The assertion `Issuer` is verified against an explicit trusted issuer allow-list (`SAML_TRUSTED_ISSUER`).
     - Explicit fail-closed attribute validation requires:
       - `subject_id`
       - `organization`
       - `organization_id`
       - `role`
       *(Note: `purpose_of_use` is represented and parsed by the audit model, but missing `purpose_of_use` is not currently included in the explicit rejection list).*
     - Missing or invalid SAML context raises a `SoapFault` returning a SOAP Sender fault over **HTTP 400** (not HTTP 401).
     - Independent cryptographic SAML XML-DSig signature verification is not currently implemented; transport-level mTLS and issuer allowlisting govern assertion authenticity.
   - **Future Hardening:** Independent cryptographic XML-DSig verification is tracked as a future security hardening consideration.

4. **NHS API Platform API Key & JWT Signing Key (JWKS)**
   - **Role:** Authenticates Xhuma to the NHS API Platform (PDS).
   - **Material:** `NHS_API_KEY` (used for `iss` and `sub`) and an RSA private key (`JWTKEY` / Key Vault secret).
   - **Mechanism:** Xhuma signs a JWT assertion (exposing a stable `kid` via `/jwk`) and exchanges it for an NHS OAuth Bearer token.

5. **Organisation Identifiers, ASID & MHS Party Key**
   - **Role:** Identifies the requesting clinical organisation and consumer system to Spine Directory Service (SDS) and downstream GP systems.
   - **Material:**
     - `ORG_CODE`: Care organisation ODS code (e.g. `RRV00` for UCLH);
     - `ORG_ASID`: Accredited System Identifier (e.g. `200000002574`);
     - `XHUMA_MHS_PARTY_KEY`: Configured MHS Party Key identifying Xhuma's consumer Device in SDS where applicable (e.g. `RRV00-824607`).
     *(Note: `ORG_CODE` and `XHUMA_MHS_PARTY_KEY` are distinct configuration settings and must not be conflated).*

6. **Relay Identity**
   - **Role:** Connects Xhuma to the HSCN network if `USE_RELAY=1`.
   - **Material:** Relay mTLS certificate + `RELAY_MTLS_ALLOWED_CERT_SHA256`.

---

## Commissioning Failure Ladder

When triaging integration failures, the sequence of trust boundaries progresses through distinct stages. Note that the full downstream chain (PDS → SDS → Relay → SSP → GP Provider) applies to the structured-record/document query journey (ITI-38 / ITI-39). ITI-55 patient discovery resolves demographics directly via PDS and does not traverse the downstream GP Connect routing layers.

1. **mTLS (Network/Transport)**: Fails if Epic presents an invalid, expired, or untrusted certificate, or if the optional thumbprint allowlist rejects it (HTTP 403).
2. **SAML (Identity)**: Fails if the assertion is missing, issuer is unknown/untrusted, or required security attributes (`subject_id`, `organization`, `organization_id`, `role`) are missing (SOAP Sender fault / HTTP 400).
3. **PDS OAuth (NHS API Auth)**: Fails if `NHS_API_KEY` is invalid, JWT is malformed, or the JWKS signature is invalid (HTTP 502 / SOAP Receiver fault HTTP 500).
4. **PDS Entitlement (API Product)**: Fails if the `NHS_API_KEY` is valid but not onboarded to the requested API product (e.g., HTTP 401/403 "No apiproduct match found").
5. **SDS (Spine Directory — Document Journey)**: Fails if the GP practice ODS code cannot be resolved to an active endpoint and ASID.
6. **Relay (Transport — Document Journey)**: Fails if the reverse relay agent is disconnected or rejects the payload.
7. **SSP (Spine Secure Proxy — Document Journey)**: Fails if the SSP routing rules reject the GP interaction or ASID.
8. **GP Provider (Endpoint — Document Journey)**: Fails if the GP clinical system is offline, times out, or returns an HTTP 500.
9. **ITI-38 / ITI-39 (Application Response)**: Xhuma transforms the retrieved GP FHIR STU3 bundle into C-CDA XML and delivers the response to Epic.
