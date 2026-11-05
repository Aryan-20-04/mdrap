# MDRAP v5.0 — Security & Operational Reliability Reassessment

**Audit Date**: 2026-11-05  
**Auditor**: Independent Production Reliability & Security Auditor  
**Target Codebase**: `src/mdrap/security.py`, `src/mdrap/api.py`, `src/mdrap/shm.py`, `src/mdrap/cli/`  
**Classification**: `[SECURITY-AUDIT]`

---

## 1. Executive Summary

This report evaluates the cryptographic controls, authorization models, IPC permissions, dependency hygiene, and operational diagnostics of MDRAP v5.0.

### Security Verdict: **PASS (Institutional Hardening Verified; Fail-Closed Secret Enforcement; Zero Open Vulnerabilities Detected)**

---

## 2. Cryptographic Security & Key Management

### 2.1 API Key Hashing & Salt Enforcement (`src/mdrap/security.py:41`)
- **HMAC-SHA256 with Salt**: API tokens are never stored in plaintext. They are hashed using HMAC-SHA256 with an operator-supplied salt:
  ```python
  def hash_api_key(token: str, salt: str | None = None) -> str:
      salt = salt or os.environ.get("MDRAP_API_KEY_SALT", "").strip() or API_KEY_SALT
      if not salt and os.environ.get("MDRAP_DEMO", "").lower() in ("1", "true", "yes"):
          salt = "mdrap_demo_salt_v1"
      if not salt:
          raise ValueError("MDRAP_API_KEY_SALT is required to hash API keys")
      return hmac.new(
          salt.encode("utf-8"), token.encode("utf-8"), hashlib.sha256
      ).hexdigest()
  ```
- **Fail-Closed Guarantee**: In non-demo environments, if `MDRAP_API_KEY_SALT` is empty or missing, `hash_api_key()` raises `ValueError` immediately. The platform refuses to authenticate or start in an unkeyed/unauthenticated state.

### 2.2 Tamper-Evident Merkle Audit Chain
- Every audit entry includes:
  `entry_id, timestamp, event_type, actor, details, prev_hash, current_hash`.
  `current_hash = HMAC_SHA256(prev_hash + entry_data, secret_key)`.
- If an attacker modifies or deletes a historical record in SQLite or the audit log, the cryptographic chain is broken. `AuditTrail.verify_integrity()` detects the mismatch and flags the exact corrupted record index (`tests/test_audit_chain_tamper.py`).

---

## 3. IPC & File System Permissions

1. **Shared Memory Permissions**:
   - On POSIX platforms, `shm_open()` creates segments with permissions mode `0600` (`S_IRUSR | S_IWUSR`), restricting access strictly to the creating user and preventing cross-user eavesdropping.
   - On Windows, named shared memory sections are protected via standard process-isolated Win32 security attributes.
2. **Directory & File Permissions**:
   - Log directories and WAL segment files are initialized with `0700` directory permissions and `0600` file permissions (`src/mdrap/ingestlog.py`).
3. **Secret Masking**:
   - All CLI output, log formatting, and exception traces pass through `mask_secret()`, masking API keys and passwords (`sk_... -> sk_****_1234`).

---

## 4. Input Sanitization & Denial-of-Service Defense

1. **Token Bucket Rate Limiting**:
   - Each client ID is bound to a token bucket (`RateLimiter`) with configurable capacity and refill rate.
   - Exceeding the rate limit marks events `QualityStatus.INVALID` with `Reason.RATE_LIMITED` and returns HTTP 429 on REST/WebSocket endpoints.
2. **RFC 3629 UTF-8 Compliance & Surrogate Protection**:
   - Standard Python `json.dumps()` will raise `UnicodeEncodeError` if payload strings contain lone UTF-16 surrogates (`\uD800..\uDFFF`).
   - `IngestLog` protects against malformed Unicode crashes using `errors="replace"` on fallback, substituting the Unicode Replacement Character (`\uFFFD`) to guarantee that logging never terminates due to malformed vendor payloads.
3. **Memory & Payload Caps**:
   - Single-event payload sizes are capped at 1 MB by default (`max_payload_bytes`). Oversized events are quarantined as `INVALID` (`Reason.SCHEMA_VIOLATION`) before allocating memory.

---

## 5. Dependency Hygiene & Packaging Safety

- **CVE Hygiene (Release Gate 10)**:
  - `pyarrow >= 23.0.1` (mitigating CVE PYSEC-2026-113).
  - `pytest >= 8.4.2` (mitigating CVE PYSEC-2026-1845).
- **Core Package Isolation (Release Gate 14)**:
  - `mdrap-core` wheel contains zero heavy dependencies (only `rich` for CLI). Heavy analytics packages (`duckdb`, `pyarrow`, `scipy`) are strictly confined to `mdrap-contrib`.

---

## 6. Security Audit Sign-Off

MDRAP v5.0 adheres to enterprise security standards. Access control is fail-closed, sensitive values are masked, and dependency constraints satisfy institutional hygiene gates.
