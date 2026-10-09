# MDRAP Phase 8 — Comprehensive Security & Threat Review

## 1. Threat Modeling across Phase 8 Additions

| Threat Vector | Affected Subsystem | Risk Description | Architectural Mitigation | Status |
|---|---|---|---|---|
| **Slow-Read Denial of Service** | `src/async_fanout.py` | Stalled malicious client intentionally stops consuming to exhaust memory or block workers. | Bounded deques (`maxlen=1000`) and automated auto-eviction after $\ge 50$ drops. | **MITIGATED** |
| **Split-Brain Desynchronization** | `src/consensus.py` | Network partition produces competing primary writers appending conflicting histories. | Monotonic epoch tokens verified at `FencedWALWriter` boundary; quorum lease renewal ($N/2 + 1$). | **MITIGATED** |
| **Cross-Tenant Symbol Leakage** | `src/async_fanout.py` | Unauthorized tenant subscribes to restricted instruments. | Strict entitlement set filtering (`subscribed_symbols`) executed prior to queue handoff. | **MITIGATED** |
| **Unauthenticated Ingress Injection** | `src/service.py`, `src/gateway_tcp.py` | Attacker connects to socket and publishes fabricated market frames. | Mandatory SHA-256 HMAC token authentication required before session registration. | **MITIGATED** |
| **Unauthorized Exchange Feed Access** | `src/itch.py`, `src/live.py` | Inadvertent connection to live exchange feeds without commercial license. | Pre-production authorization gate strictly enforces test-only and simulated data modes. | **MITIGATED** |

## 2. Cryptographic & Security Verification
- **API Key Security**: Secrets stored as salted SHA-256 hashes; plain-text keys never logged or stored.
- **Audit Log Tamper-Resistance**: Every canonical event persists a SHA-256 Merkle root leaf hash.
- **Shared Memory Permissions**: POSIX SHM segments created with strict `0600` permissions.
