# MDRAP Security Threat Model & Baseline Controls (STRIDE)

> **Document ID**: `MDRAP-SEC-THREAT-MODEL-V4`  
> **Release Target**: MDRAP v4.0.0  
> **Classification**: Technical Security Specification & Compliance Baseline  
> **Scope**: Ingress Gateways, IngestLog WAL, Engine Core, SQLite Projection, IPC Shared Memory, TCP Service Fanout, REST/WebSocket API.

---

## 1. Security Architecture & Threat Modeling Framework

MDRAP serves as an institutional market-data validation and normalization sidecar sitting between external untrusted/semi-trusted exchange feeds and downstream trading desks, quantitative analytics, and tick databases. 

Security evaluations follow the **STRIDE** methodology (Spoofing, Tampering, Repudiation, Information Disclosure, Denial of Service, Elevation of Privilege) combined with zero-trust perimeter assumptions:

```
[External World / Exchanges]
        │
        ▼ (TLS / HMAC Signature / Strict Ingress Bounds)
┌─────────────────────────────────────────────────────────────┐
│ MDRAP Ingress Boundary (Gateway / WebSocket / TCP)          │
│ • InputSanitizer (Finite bounds, ASCII tickers, max 1MB)    │
│ • CIDRFilter & IP Brute-Force Lockout                       │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ IngestLog WAL (Durability & Audit Root)                     │
│ • Append-only, CRC-32 Frame Guard, Advisory File Lock        │
│ • Merkle Audit Tree (RFC 6962 Domain Separation)            │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ Engine & In-Memory State Fold                               │
│ • Deterministic fold: (state, event, clock) -> decision     │
│ • Monotonic Quality Invariant: INVALID > SUSPICIOUS > VALID │
│ • Salted PBKDF2 / HMAC API Key Store (Constant-Time Compare)│
└──────────────┬──────────────────────────────┬───────────────┘
               │                              │
               ▼ (0600 POSIX SHM)             ▼ (mTLS / TLS)
┌──────────────────────────────┐ ┌────────────────────────────┐
│ Co-Located SHM Ring Buffer   │ │ TCP / WebSocket Fanout     │
│ • Seqlock double-check read  │ │ • Non-blocking async queue │
│ • Atomic commit barriers     │ │ • Role-based entitlements  │
└──────────────────────────────┘ └────────────────────────────┘
```

---

## 2. STRIDE Threat Matrix & Enforced Controls

| STRIDE Category | Target Asset / Component | Threat & Attack Vector | Enforced Mitigation & Security Control | Verification Test / Gate |
|---|---|---|---|---|
| **Spoofing** | REST API & WebSockets | Attacker impersonates an authorized trading desk using forged or truncated API tokens. | **Salted HMAC Key Store**: API tokens are salted using `MDRAP_API_KEY_SALT` and hashed with HMAC-SHA256 (`hash_api_key`). Tokens are never stored in plaintext. Key IDs are 16-hex deterministic hashes (64-bit entropy) to prevent prefix collisions. Comparisons use `hmac.compare_digest`. | `test_api_auth.py`<br>`test_key_storage_hardening.py` |
| **Spoofing** | Feed Ingress Gateway | Rogue feed source injects forged ticks claiming to originate from NASDAQ or CME. | **Cryptographic HMAC Payload Signatures**: Inbound feed payloads can be signed via shared secrets; `security.verify_payload()` rejects forged signatures before engine step. | `test_security.py`<br>`test_phase3_security.py` |
| **Tampering** | IngestLog WAL | Malicious actor or local bit-flip modifies stored historical events in segment files. | **Per-Frame CRC-32 & Merkle Chain**: Every WAL frame has a 24-byte prefix + payload CRC-32 checksum. Historical rows commit into a cryptographic Merkle tree (RFC 6962 prefixes `0x00` leaf, `0x01` node). Tampered frames fail CRC and halt ingestion. | `test_audit_chain_tamper.py`<br>`test_wal_format_v2.py` |
| **Tampering** | Engine Quality Evaluation | Malicious actor attempts to downgrade an `INVALID` event to `VALID` to poison downstream trading books. | **Monotonic Status Priority**: `QualityStatus` strictly enforces `INVALID (2) > SUSPICIOUS (1) > VALID (0)`. Once marked INVALID, an event can never be promoted or downgraded. | `test_canonical_models.py`<br>`test_quality.py` |
| **Repudiation** | Canonical Decisions & Trade Reconciliations | Counterparty disputes execution price or claims MDRAP dropped an in-flight trade. | **Cryptographic Provenance Lineage**: Every emitted `CanonicalDecision` records chosen source, competing prices, arrival timestamps, and reason bitmasks. Quarantine stores original vendor JSON payload verbatim. | `test_reconciliation_invariants.py`<br>`test_quarantine_subsystem.py` |
| **Information Disclosure** | Memory Dumps & Heap Analysis | Memory inspection leaks active API tokens or HMAC shared secrets. | **Salted Digest Storage**: Heap structures (`HashedKeyStore`) store only salted digests. Plaintext tokens are stripped immediately upon registration (`ent.token = ""`). Plaintext tokens are strictly forbidden in logs (`logger.*`). | `test_hardening_security.py`<br>`test_institutional_security.py` |
| **Information Disclosure** | Shared Memory (IPC) | Local unprivileged process on the host reads proprietary market data streams. | **Strict Permissions**: POSIX SHM files are created with mode `0600` (read/write only by owner process). Windows named objects are scoped to local session. | `test_shm.py`<br>`test_transport_hardening.py` |
| **Information Disclosure** | Webhooks & Alert Sinks | Server-Side Request Forgery (SSRF) targeting cloud metadata endpoints (`169.254.169.254`). | **SSRF IP Filter**: DNS pre-resolution blocks loopback (`127.0.0.0/8`), private RFC 1918 subnets (`10/8`, `172.16/12`, `192.168/16`), link-local, carrier-grade NAT, and cloud metadata IPs. HTTP redirects are strictly disallowed. | `test_alerts.py` |
| **Denial of Service** | REST Ingest & WebSocket Server | Malicious client floods endpoints with malformed JSON, high IP churn, or poison floats ($10^{400}$). | **InputSanitizer & TokenBucket**: 1MB payload collar, finite float verification (`math.isfinite`), 63-bit sequence bounds, and thread-safe token bucket rate limiter with IP lockout (10 failed auths = 60s quarantine). | `test_adversarial_fuzzing.py`<br>`test_api_security_hardening.py` |
| **Denial of Service** | TCP Fan-out Service | Stalled slow consumer holds socket buffers, exhausting server memory. | **Leaky-Bucket Eviction**: Each client has an isolated bounded queue (1,000 frames). Overflows drop oldest frames with drop counters; consumers exceeding eviction drop thresholds are severed. | `test_async_fanout.py`<br>`test_service.py` |
| **Elevation of Privilege** | Admin APIs (`POST /v1/keys`, `POST /v1/chaos`) | Viewer key attempts to issue admin commands, trigger chaos drills, or unblock sources. | **3-Tier Strict RBAC Hierarchy**: `VIEWER (1) < OPERATOR (2) < ADMIN (3)`. Dependencies enforce `require_role(Role.ADMIN)`. Role escalation attempts raise HTTP 403 `AccessDenied`. | `test_phase4_security.py`<br>`test_institutional_security.py` |

---

## 3. Mandatory Security Baseline Controls

All MDRAP components and future extensions must adhere to these five non-negotiable rules:

### Control 1: Salted Hashes & Constant-Time Verification
- Direct comparisons like `token == stored_token` are strictly banned.
- All token verification must use `hmac.compare_digest(stored_digest, computed_digest)`.
- Storage of API keys must use salted SHA-256 via `hash_api_key(token, salt)`.
- Environment variable `MDRAP_API_KEY_SALT` is mandatory in production deployments.

### Control 2: Zero Plaintext Token Logging
- Tokens, secrets, and raw Authorization headers must **never** appear in log outputs, exception tracebacks, or HTTP error responses.
- HTTP requests extract tokens strictly from headers (`X-API-Key` or `Authorization: Bearer`), never from URL query parameters (which leak into proxy access logs).

### Control 3: Monotonic Quality State Invariant
- Data quality status merges must always take the worse status:
  $$\text{Merged Status} = \max(\text{Status}_A, \text{Status}_B)$$
- No rule, hook, plugin, or reconciler may ever downgrade `INVALID` to `SUSPICIOUS` or `VALID`.

### Control 4: Input Bounds Sanitization
- All prices, sizes, and timestamps must be validated for finiteness: `not math.isnan(x) and not math.isinf(x) and x >= 0` (unless negative price is explicitly enabled for commodity futures).
- Strings (symbols, sources) must conform to strict ASCII character sets: `^[A-Za-z0-9/_\-.:=^ ]{1,32}$`.

### Control 5: Least Privilege Packaging
- Container images (`Dockerfile`) must run as non-privileged user `mdrap:mdrap` (UID 1000).
- The root filesystem must be mounted read-only, with write permissions restricted strictly to the `/data` volume.
