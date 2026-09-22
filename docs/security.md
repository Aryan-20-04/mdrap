# MDRAP Platform Security & Cryptographic Posture

This document details the security architecture, authentication lifecycle, Role-Based Access Control (RBAC), and cryptographic guarantees implemented in MDRAP.

---

## 1. Threat Model & Design Principles

MDRAP processes high-velocity, mission-critical market data. The platform enforces five foundational security invariants:

1. **Zero Raw Secret Exposure**: Plaintext API tokens are generated once and never persisted in database storage.
2. **Cryptographic Feed Verification**: Inbound feeds can be validated using HMAC-SHA256 with constant-time equality comparisons to prevent timing attacks.
3. **Strict Privilege Boundaries**: Every administrative and operational action requires explicit role entitlements.
4. **Append-Only Merkle Audit Trail**: Critical operational events form an unbroken cryptographic hash chain. Any tampering, deletion, or reordering breaks the chain verification.
5. **Denial-of-Service Defense**: Token bucket rate limiting defends downstream execution engines from ingress packet flooding.

---

## 2. API Key Management & Hashed Storage

### Token Lifecycle

When an API key is generated (via CLI or `POST /v1/keys`), MDRAP executes the following workflow:

```
1. Generate Raw Key:
   token = "mdrap_live_" + urlsafe_token(32)

2. Compute SHA-256 Hash:
   token_hash = sha256(token.encode('utf-8')).hexdigest()

3. Generate Masked Prefix:
   key_prefix = token[:12] + "..."

4. Persist to Database:
   INSERT INTO api_keys (token_hash, key_prefix, client_id, role, ...)

5. Display to Administrator:
   The plaintext token is displayed EXACTLY ONCE to the administrator.
```

### Storage Protection Guarantee
- The SQLite table `api_keys` contains **only** `token_hash` and `key_prefix`.
- Even in the event of a raw database snapshot, disk leak, or storage backup exfiltration, attackers **cannot** recover valid API keys.
- On authentication, incoming requests present `X-API-Key: <token>` or `Authorization: Bearer <token>`. The engine computes `sha256(token)` in memory and matches it against `token_hash`.

### Automatic Legacy Schema Migration
For existing deployments with older schemas, MDRAP automatically upgrades the database table on startup:
- Plaintext `token` values in legacy tables are extracted, hashed with SHA-256, and safely swapped into `token_hash`.
- The legacy table is safely dropped, permanently removing plaintext secrets from the filesystem.

---

## 3. Role-Based Access Control (RBAC)

MDRAP enforces a three-tier hierarchical privilege model:

$$\text{VIEWER} \subset \text{OPERATOR} \subset \text{ADMIN}$$

| Role | Hierarchy Level | Allowed Capabilities | Restricted Actions |
|---|---|---|---|
| **VIEWER** | 1 | Read platform health, query validated canonical events, view quality summaries, inspect live NBBO and L2 depth ladders, stream real-time events over WebSocket. | Cannot inspect quarantine records, cannot alter feed configurations, cannot manage keys. |
| **OPERATOR** | 2 | All VIEWER capabilities PLUS: inspect quarantined invalid records, run feed ingestion controls, verify and export cryptographic audit proofs. | Cannot generate or revoke API keys, cannot block/unblock feed providers permanently. |
| **ADMIN** | 3 | Full platform authority: generate and revoke API keys, assign RBAC roles, register/block feed sources, alter engine configurations. | None. |

---

## 4. Tamper-Evident Merkle Audit Trail (§19)

Every security event, administrative action, feed status change, and configuration modification is recorded in an append-only cryptographic audit chain.

### Mathematical Hash Chaining Formula

For audit entry $i$, the entry hash $H_i$ is computed as:

$$H_i = \text{SHA256}\Big(H_{i-1} \,\|\, t_i \,\|\, \text{actor}_i \,\|\, \text{role}_i \,\|\, \text{action}_i \,\|\, \text{details}_i\Big)$$

where:
- $H_0 = \text{GENESIS\_0000000000000000000000000000000000000000000000000000000000000000}$
- $t_i$ is the Unix timestamp with microsecond resolution.
- $\|$ denotes canonical delimited concatenation.

### Audit Verification & Standalone Proof Export
- **Online Verification**: `GET /v1/audit/verify` verifies the full chain from genesis to the current tip.
- **Standalone Proof Bundle**: `GET /v1/audit/export` outputs a self-contained, cryptographically sealed JSON document that can be handed to external auditors, regulatory examiners, or risk committees without granting them database access.
- Proof bundles can be verified offline using:
  ```bash
  python cli.py audit --export-proof audit_bundle.json
  python cli.py audit --verify-proof audit_bundle.json
  ```

---

## 5. Feed HMAC Authentication

For proprietary inter-firm feeds and high-security private broker lines, MDRAP supports symmetric cryptographic message authentication:
- **Algorithm**: `HMAC-SHA256`
- **Verification**: Evaluated using Python's `hmac.compare_digest()` to ensure constant-time execution and prevent side-channel timing attacks.
- **Anti-Replay Protection**: Packets with monotonic sequence regressions or timestamps older than staleness thresholds are tagged with `REASON=SECURITY_REJECT` and quarantined.
