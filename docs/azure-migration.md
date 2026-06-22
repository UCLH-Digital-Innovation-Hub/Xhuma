# Relay + Epic mTLS: migrating from DigitalOcean to Azure

Status: the HSCN relay is mTLS-secured and **working on DigitalOcean** (Xhuma `relay-mtls`
branch, nginx in front, in-process relay hub). This note captures what changes when Xhuma
and the relay endpoint move to **Azure App Service**.

## Current (DigitalOcean) architecture — for context

```
relay-client (NHS firewall)  ──outbound mTLS wss──▶  nginx (relay.xhumademo.com)
   presents client.crt (private CA)                  ssl_verify_client on (vs private CA)
   trusts the PUBLIC relay cert (CA_CERT_PATH="")     forwards cert as X-Relay-ClientCert
                                                      proxy_pass ──▶ xhuma:80  (/relay/ws)
   ──NHS mTLS──▶ NHS APIs
```

Two layers of mTLS on the relay path:
1. **nginx** `ssl_verify_client on` against the private CA (`/etc/nginx/client-ca/ca.crt`).
2. **App layer** — `app/relay/routes.py:_enforce_relay_mtls` pins the client cert by **SHA-256
   fingerprint** (`RELAY_MTLS_ALLOWED_CERT_SHA256`), reading the cert from the
   `X-Relay-ClientCert` header that nginx forwards (`$ssl_client_escaped_cert`).

## The key Azure constraint

**Azure App Service terminates TLS itself and cannot do raw TLS passthrough.** So the
nginx-based mTLS edge does not move to Azure as-is — it is replaced by App Service's
client-certificate feature:

- App Service validates/forwards the client cert to the app as the **`X-ARR-ClientCert`**
  header (base64 PEM) when `client_certificate_enabled = true`.
- It does **not** run `ssl_verify_client`. With `client_certificate_mode = "Optional"` it
  forwards whatever cert is presented but does **not** enforce that it chains to our private CA.

`infra/main.tf` already sets `client_certificate_enabled = true` and
`client_certificate_mode = "Optional"`.

## What carries over unchanged

- **The app-layer fingerprint pin** (`app/relay/routes.py`). Just point it at the Azure header:
  set `RELAY_CLIENT_CERT_HEADER=X-ARR-ClientCert`. Keep `RELAY_MTLS_ALLOWED_CERT_SHA256` set to
  the relay client cert's fingerprint (or leave it unset to accept any presented cert).
- `_parse_client_cert_from_header` already accepts **both** URL-encoded PEM (nginx) and
  base64 DER/PEM (Azure `X-ARR-ClientCert`) — no code change needed.
- **The relay client config** — it still presents its `client.crt` and trusts the public
  server cert (`CA_CERT_PATH=""`). Only `RELAY_SERVER_URL` changes to the Azure hostname.

## Migration checklist

1. **App Service**: `client_certificate_enabled = true`, choose `client_certificate_mode`
   (Optional vs Require), and **`websockets_enabled = true`** (the relay holds a long-lived WS).
2. **App settings**: `RELAY_CLIENT_CERT_HEADER=X-ARR-ClientCert`; keep
   `RELAY_MTLS_ALLOWED_CERT_SHA256` (the app-layer pin is now the real gate, since App Service
   doesn't verify against our CA). Optionally also verify issuer == `HSCN Relay Private CA`.
3. **DNS + public cert** for the Azure relay hostname; update the relay client's
   `RELAY_SERVER_URL` (e.g. `wss://relay.<azure-host>/relay/ws/client1`).
4. **Relay client (NHS firewall box) is unchanged** — it keeps its squid `WS_PROXY`/`HTTPS_PROXY`
   for HSCN egress; none of that is Azure-side.
5. **Drop the nginx relay vhost** (the Azure front end replaces it); keep
   `app/relay/routes.py` enforcement as-is.

## Epic ↔ Xhuma (separate mTLS channel — don't conflate with the relay)

- Epic → Xhuma IHE/SOAP uses its own mutual TLS. On Azure this is handled by App Service
  (`X-ARR-ClientCert`) + `app/middleware/mtls.py` (`MTLSMiddleware`, gated by `REQUIRE_MTLS`),
  which currently only checks **header presence** — consider hardening it to validate the
  client cert's thumbprint/issuer (flagged as a TODO in that file).
- On the **DigitalOcean nginx the public `location /` block does no client-cert handling**, so
  Epic's mutual TLS is a **DigitalOcean-only gap**; Azure App Service restores it.
- Epic presents its own client cert from its Windows store. A `CertificateLookupException`
  ("failed to find a valid client certificate among '<thumbprint>'") is an **Epic-side**
  expired/rotated-cert problem — check `NotAfter`/`HasPrivateKey` for that thumbprint in
  `Cert:\LocalMachine\` on the Epic interconnect server, and keep both ends' Care Everywhere
  config pointing at the current cert.
