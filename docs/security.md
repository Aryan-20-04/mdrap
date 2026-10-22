# MDRAP Platform Security & Cryptographic Posture

This document details the security architecture, authentication lifecycle, Role-Based Access Control (RBAC), and cryptographic guarantees implemented across MDRAP.

---

## 1. Threat Model & Design Principles

MDRAP processes high-velocity, mission-critical financial market data. The platform enforces five foundational security invariants:

1. **Zero Plaintext Secret Storage**: Raw API tokens are generated once and never persisted to non-volatile disk. Storage records contain only salted cryptographic hashes.
2. **Mandatory Cryptographic Salt**: Hashing requires a minimum 16-character private salt configured via `MDRAP_API_KEY_SALT`. Service startup outside demo mode fails immediately if this salt is omitted.
3. **Timing-Attack Resistance**: Authentication token verification and HMAC signature validation strictly employ `hmac.compare_digest()` constant-time equality checks.
4. **Strict Role-Based Access Control (RBAC)**: All administrative, operational, and data query endpoints require explicit role authorizations.
5. **Tamper-Evident SHA-256 Merkle Audit Chain**: Every security action, configuration update, and persisted state transition forms an unbroken hash chain. Any out-of-band database modification immediately invalidates chain verification.

---

## 2. API Key Lifecycle & Salted Hashing

### Key Generation & Storage Flow

```text
1. Generate Raw Token:
   token = "mdrap_live_" + secrets.token_urlsafe(32)

2. Apply Environment Salt:
   token_hash = sha256((token + os.environ["MDRAP_API_KEY_SALT"]).encode("utf-8")).hexdigest()

3. Derive Deterministic Key ID:
   key_id = token_hash[:16]      (64 bits entropy, safe for revocation without leaking secret)
   key_prefix = token[:12] + "..."

4. Persist Entitlement:
   INSERT INTO api_keys (key_id, token_hash, key_prefix, client_id, role, is_active)

5. One-Time Token Emission:
   The raw token is returned to the client once and discarded from server memory.
```

### Authentication Verification
When a request arrives with `Authorization: Bearer <token>`:
1. The server reads `<token>` from the header.
2. Computes `candidate_hash = sha256((token + salt).encode("utf-8")).hexdigest()`.
3. Performs a constant-time check via `hmac.compare_digest(candidate_hash, stored_hash)`.

---

## 3. Role-Based Access Control (RBAC)

MDRAP enforces a strict three-tier privilege hierarchy:

$$\text{VIEWER} \subset \text{OPERATOR} \subset \text{ADMIN}$$

| Role | Level | Permitted Actions | Restrictions |
| :--- | :---: | :--- | :--- |
| **`VIEWER`** | 1 | Query health endpoints, stream real-time events over WebSocket, inspect BBO quotes and L2 depth, query canonical events. | Cannot view quarantine records, cannot modify feed configs, cannot manage keys. |
| **`OPERATOR`** | 2 | All `VIEWER` privileges plus: query quarantined invalid events, trigger feed ingestion controls, execute audit integrity verification. | Cannot create or revoke API keys, cannot alter engine configuration parameters. |
| **`ADMIN`** | 3 | Full platform authority: generate and revoke API keys, assign RBAC roles, register feed sources, adjust engine thresholds, export audit bundles. | None. |

---

## 4. Tamper-Evident Merkle Audit Trail

Every operational action, configuration modification, and state change writes an append-only entry into SQLite `audit_log`:

### Mathematical Hash Chaining Formula
For entry sequence $i \ge 1$:

$$H_i = \text{SHA-256}\Big(H_{i-1} \parallel t_i \parallel \text{actor}_i \parallel \text{role}_i \parallel \text{action}_i \parallel \text{details}_i\Big)$$

Where:
- $H_0 = \text{"GENESIS\_0000000000000000000000000000000000000000000000000000000000000000"}$
- $t_i$ is the Unix timestamp with nanosecond/microsecond resolution.
- $\parallel$ denotes pipe-delimited canonical string serialization.

### Verification & Offline Audit Packages
- **In-Process Verification**:
  ```python
  from mdrap.storage import Store
  store = Store("data/mdrap.db")
  valid, msg, count = store.verify_audit_integrity()
  assert valid is True
  ```
- **CLI Verification & Export**:
  ```bash
  mdrap audit verify --db data/mdrap.db
  mdrap audit export --db data/mdrap.db --out audit_package.zip
  ```

---

## 5. Network & IPC Security

- **Loopback Default**: API and daemon bind to `127.0.0.1` by default to avoid unintended exposure on public network interfaces.
- **Shared Memory Permissions**: POSIX shared memory segments created by `mdrap.shm` are opened with mode `0600` (read/write restricted exclusively to the process owner user ID).
- **Socket Backpressure Defense**: Saturated sockets drop oldest unconsumed frames with explicit drop counters (`_drop_count`), preventing out-of-memory denial-of-service across the engine.
