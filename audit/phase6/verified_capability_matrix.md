# MDRAP Phase 6 — Verified Capability and Support Matrix

## 1. Executive Summary & Integrity Classification
This matrix provides the authoritative institutional support classification for all features, protocols, and deployment models within the Market Data Reliability & Acceleration Platform (MDRAP). We strictly distinguish between capabilities verified with reproducible production evidence and those that remain experimental or unsupported.

---

## 2. Platform Capability Matrix

| Subsystem / Capability | Specific Feature | Maturity Status | Evidence Reference | Operating Envelope & Boundaries |
| :--- | :--- | :--- | :--- | :--- |
| **Ingress & Normalization** | ITCH 5.0, BATS, Polygon, SBE decoders | **PRODUCTION-SUPPORTED** | `src/gateway.py`, `src/adapters/` | Deterministic replay feeds & public WebSockets. |
| **Ingress Normalization** | Nullable sequence / timestamp handling | **PRODUCTION-SUPPORTED** | `tests/test_ws_feed.py` | Sets `clock_source="GATEWAY_RECV"` when missing. |
| **Data Quality Engine** | Multi-rule checks, Welford anomaly detection | **PRODUCTION-SUPPORTED** | `src/quality.py`, `rules.def` | Rule priority: `INVALID > SUSPICIOUS > VALID`. |
| **Quarantine Storage** | Bitmask-tagged SQLite quarantine store | **PRODUCTION-SUPPORTED** | `src/quarantine.py`, `quarantine.db` | Retains full raw payload and reason codes. |
| **Cross-Feed Reconciliation** | Multi-source arbitration & provenance tracking | **PRODUCTION-SUPPORTED** | `src/reconciliation.py` | Provenance tracked; zero cache mutation. |
| **Durability Boundary** | `IngestLog` WAL with CRC32 segment rotation | **PRODUCTION-SUPPORTED** | `src/ingestlog.py`, `events.seg` | Group fsync (`fsync_policy="grouped_by_size"`). |
| **Shared Memory IPC** | Lock-free SPSC seqlock ring buffer | **PRODUCTION-SUPPORTED** | `src/shm.py`, `fastpath.c` | Sub-microsecond local reader distribution. |
| **Wire Protocol** | Simple Binary Encoding (SBE) stream | **PRODUCTION-SUPPORTED** | `src/sbe.py`, TCP port 9002 | 64-byte binary frame; sequence gap audited. |
| **Security & RBAC** | Salted PBKDF2/SHA-256 API tokens | **PRODUCTION-SUPPORTED** | `src/security.py` | 16-hex `key_id` deterministic instant revocation. |
| **Usage Accounting** | Daily EOD unit-of-count metering & Merkle audit | **PRODUCTION-SUPPORTED** | `src/metering.py`, `usage_metering` | Tamper-evident HMAC-SHA256 audit roots. |
| **Deployment Tooling** | Preflight automated installer & dry-run linter | **PRODUCTION-SUPPORTED** | `scripts/deploy_pilot.py` | Enforces CPU, RAM, disk, Python >= 3.11. |
| **Forensic Diagnostics** | Sanitized bundle capture with secret redaction | **PRODUCTION-SUPPORTED** | `scripts/diagnostic_bundle.py` | 100% regex credential and token scrubbing. |
| **High Availability** | Active-Passive node replication | **EXPERIMENTAL** | `src/failover.py` | Prototyped in Phase 3; fencing not certified. |
| **Kernel Bypass NICs** | Solarflare Onload / DPDK / AF_XDP drivers | **UNSUPPORTED IN SANDBOX** | `audit/phase3/` | Requires physical enterprise NIC hardware. |
| **Proprietary Exchange Drops**| Direct optical cross-connects (NY4/Carteret) | **UNSUPPORTED IN SANDBOX** | `audit/phase5/` | Validated via high-fidelity replay simulation. |

---

## 3. Maturity Status Definitions

- **PRODUCTION-SUPPORTED**: Implemented, verified by unit/integration tests, benchmarked under sustained load, equipped with operational runbooks, and certified for Profile A.
- **EXPERIMENTAL**: Prototyped in source code with preliminary tests, but lacking hard distributed fencing or extensive failure injection verification.
- **UNSUPPORTED IN SANDBOX**: Architecturally evaluated and documented, but not deployed or certified due to lack of physical hardware or proprietary circuits in the current operating environment.
