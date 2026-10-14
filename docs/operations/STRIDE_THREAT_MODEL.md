# MDRAP STRIDE Security Threat Model & Control Matrix

**Version**: 3.1.0  
**Scope**: Market Data Ingest, REST API, WebSocket streams, TCP Gateway, Shared Memory (SHM), and Webhooks.

---

## 1. Threat Matrix by STRIDE Category

| Threat Category | System Component | Potential Vector | Mitigation & Technical Control | Status |
|---|---|---|---|---|
| **Spoofing** | REST API & WebSockets | Attacker presents forged bearer token or spoofed venue source. | Constant-time token verification (`hmac.compare_digest`), cryptographic salted key storage (`MDRAP_API_KEY_SALT`), pre-handshake WebSocket rejection (`code=1008`). | **ENFORCED** |
| **Tampering** | IngestLog WAL & SQLite | Malicious process or bit-flip alters stored historical market events. | Per-frame CRC32 checksums, append-only segment immutability, Merkle tree audit log linking events to deterministic roots. | **ENFORCED** |
| **Repudiation** | Reconciled Canonical Stream | Disputed order executions or unacknowledged trades. | Immutable RawArchive write-ahead logging, deterministic sequence number assignment, complete cryptographic lineage metadata. | **ENFORCED** |
| **Information Disclosure** | Alert Webhooks & IPC SHM | SSRF attacks via malicious webhook targets; unauthorized local memory inspection. | DNS pre-resolution blocking private/loopback/cloud metadata IP ranges, redirect disabling, POSIX SHM permissions restricted to `0600`. | **ENFORCED** |
| **Denial of Service** | Network Gateways & Watchdog | Slow consumers block engine broadcast; high-frequency client flooding; Merkle scan scrape storms. | Dedicated non-blocking writer threads per client with bounded queues; token-bucket rate limiter with global backstop; TTL-cached Merkle roots for Prometheus scrapes. | **ENFORCED** |
| **Elevation of Privilege** | Admin Endpoints & CLI | Low-privilege viewer acquires admin keys or accesses raw SQLite files. | Strict RBAC role enforcement (`VIEWER`, `TRADER`, `ADMIN`), admin hash timing attack prevention, mandatory environment salt bootstrap outside demo mode. | **ENFORCED** |

---

## 2. Attack Surface Boundaries & Controls

### 2.1 Webhook & Alert Sink Boundary (SSRF Defense)
- Pre-resolves domain names prior to connection.
- Denies connections to loopback (`127.0.0.0/8`, `::1`), private networks (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), Carrier-Grade NAT (`100.64.0.0/10`), link-local (`169.254.0.0/16`, `fe80::/10`), Unique Local Addresses (`fc00::/7`), and cloud metadata (`169.254.169.254`).
- Custom HTTP redirect handlers explicitly disallow following redirect chains.

### 2.2 Network Gateways (TCP / WebSocket)
- Bounded ingress payload ceiling: 1 MB max frame size collar.
- Handshake timeout collars and per-IP connection limits.
- Client token authentication occurs *prior* to socket accept or frame queuing.

### 2.3 Shared Memory (IPC Boundary)
- POSIX SHM segment created with `mode 0600` (read/write by owner process only).
- Seqlock generation counter protocol with `UNCOMMITTED` bitflag prevents readers from consuming partially written slots.
- Hardware memory barriers (`dmb ish` on ARM, compiler memory clobbers on x86-64) enforce acquire/release consistency.
