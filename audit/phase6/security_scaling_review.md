# MDRAP Phase 6 — Security Architecture Review at Expanded Scale

## 1. Executive Summary & Expanded Threat Surface
Scaling from a single-node engine to a multi-instance partitioned fleet introduces additional network listening ports, inter-shard process boundaries, and multi-tenant resource interactions. This security review reassesses the platform's threat model at scale to ensure that horizontal scaling does not create lateral movement vulnerabilities, privilege escalation, or data exfiltration hazards.

---

## 2. Expanded Attack Surface Analysis & Controls

```
┌─────────────────────────────────┐      ┌─────────────────────────────────┐
│     EXTERNAL FEED NETWORK       │      │       TRADING LAN / CONSUMERS   │
└────────────────┬────────────────┘      └────────────────┬────────────────┘
                 │ (Feed Ingress)                         │ (SBE Sockets 9002, 9003)
                 ▼                                        ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                   MDRAP MULTI-SHARD HOST (PHASE 6)                       │
│                                                                          │
│  - Shard 0 (Port 9002): Isolated Directory /var/data/mdrap/shard_0       │
│  - Shard 1 (Port 9003): Isolated Directory /var/data/mdrap/shard_1       │
│  - Filesystem Fencing: shard.lock per instance (Perms: 0600)             │
│  - Fleet Telemetry API: Port 8000 (Bearer Token Required: Role.VIEWER)   │
│  - Administrative API:  Port 8000 (Bearer Token Required: Role.ADMIN)    │
└──────────────────────────────────────────────────────────────────────────┘
```

| Threat Vector | Severity | Architectural Vulnerability | Enforced Compensating Control |
| :--- | :--- | :--- | :--- |
| **Port Scanning & Eavesdropping** | High | Additional open SBE ports (9002, 9003) exposed to network. | Each port enforces mandatory PBKDF2 token handshake within 1,000 ms before emitting frames. |
| **Cross-Shard File Hijacking** | Medium | Process misconfiguration targeting wrong shard WAL directory. | Exclusive OS filesystem lock (`shard.lock`) and epoch verification fail-closed. |
| **Telemetry Secret Leakage** | Medium | `/health/fleet` exposing tenant IDs or internal auth tokens. | Strict JSON schema sanitization; internal hashes and secrets scrubbed from responses. |
| **Noisy-Neighbor Denial of Service**| High | One tenant flooding socket connections to starve peer desks. | `TenantQuotaManager` rate limiting; independent bounded queues per client. |

---

## 3. Cryptographic and Access Invariants

1. **Least Privilege Process Model**: All shards run under unprivileged user `mdrap:mdrap` (UID 1001). No root capabilities permitted.
2. **Deterministic Token Revocation**: Revocation operates by deterministic 64-bit `key_id = token_hash[:16]`, ensuring O(1) revocation across all shards simultaneously.
3. **Audit Trail Sealing**: Control plane actions and usage accounting are cryptographically sealed with HMAC-SHA256.
