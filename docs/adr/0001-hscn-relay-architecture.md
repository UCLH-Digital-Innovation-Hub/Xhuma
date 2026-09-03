# ADR 0001: HSCN Relay Architecture

## Status
Accepted (Implemented: Mid-June 2026)

## Context
Xhuma operates on Azure over the public internet, but it needs to securely query the NHS GP Connect infrastructure which is isolated on the Health and Social Care Network (HSCN). Establishing an inbound VPN or opening inbound firewall ports on the HSCN side poses significant security and operational challenges.

## Decision
We adopted an asynchronous, outbound WebSocket-based Relay Client to bridge the HSCN gap.
- **Relay Hub**: Xhuma hosts a WebSocket hub natively within its API (`/relay/ws/{client_id}`).
- **Relay Client**: A container inside the HSCN boundary establishes an *outbound-only* persistent connection to the Xhuma hub.
- **Traffic Tunneling**: Xhuma tunnels outbound GP Connect requests through this WebSocket connection to the Relay Client, which executes the request on the HSCN network and returns the payload.

## Consequences

### Positive
- **Security**: The HSCN environment requires zero inbound open ports. The Relay Client only initiates outbound connections, drastically reducing the attack surface.
- **Simplicity**: Eliminates the need for complex site-to-site VPNs or ExpressRoute configurations.
- **Certificate Management**: The Relay Client manages the strict NHS PKI mTLS authentication directly, avoiding certificate propagation complexities through Xhuma.

### Negative
- **Latency**: Introduces a minor latency overhead due to WebSocket tunneling and serialization.
- **Connection Drops**: If the WebSocket drops, GP Connect queries will fail until the client reconnects (handled by `RECONNECT_DELAY_SECS`).
