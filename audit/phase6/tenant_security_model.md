# MDRAP Phase 6 — Multi-Tenant Security & Isolation Model

## 1. Executive Summary & Security Objectives
In a multi-tenant institutional environment, security isolation must guarantee that no tenant can eavesdrop on unauthorized market data streams, tamper with audit trails, degrade peer tenant performance, or execute unauthorized administrative commands. This document details the cryptographic boundaries, access controls, and threat mitigations enforced in Phase 6.

---

## 2. Multi-Tenant Security Boundaries

| Security Domain | Boundary Mechanism | Enforcement Layer | Failure Behavior |
| :--- | :--- | :--- | :--- |
| **Authentication** | PBKDF2/SHA-256 salted token hash | Gateway Ingress Handshake | `401 Unauthorized`; immediate socket close. |
| **Revocation** | 64-bit deterministic `key_id` | In-memory Entitlement Registry | Immediate session termination (< 50 ms). |
| **Data Entitlement** | Venue/Symbol Whitelist | SBE / Socket Dispatch Loop | Unentitled ticks omitted from frame dispatch. |
| **Resource Quotas** | Token-bucket rate limiter & max subs | `TenantQuotaManager` | Rate-limited requests throttled or rejected. |
| **Audit Non-Repudiation** | HMAC-SHA256 Merkle root | `src/metering.py` | Billing logs cryptographically sealed EOD. |
| **Administrative Access** | `Role.ADMIN` token requirement | REST & CLI Management API | `403 Forbidden` on non-admin token. |

---

## 3. Threat Model & Mitigations

### Threat 1: Tenant Spoofing / Impersonation
- **Attack Vector**: Attacker attempts to use another trading desk's `client_id` to evade rate limits or access premium feeds.
- **Mitigation**: `client_id` is never accepted as an unverified parameter; it is deterministically extracted from the securely hashed API bearer token stored in the SQLite authorization table.

### Threat 2: Noisy-Neighbor Resource Exhaustion
- **Attack Vector**: Tenant launches an aggressive quantitative backtest that floods the socket listener with snapshot requests.
- **Mitigation**: `TenantQuotaManager.record_and_check_rate()` rejects requests exceeding the tenant's provisioned queries-per-second, protecting the engine's core ingestion budget.

### Threat 3: Unauthorized Feed Eavesdropping
- **Attack Vector**: Tenant entitled only to NASDAQ tries to decode CME futures tick frames.
- **Mitigation**: Kernel-level wire filter drops non-entitled frames before serialization.
