# MDRAP v5.0 Security, Authentication & Access Control Audit Findings

## 1. Scope & Verification Strategy

This audit inspects the security controls, authentication mechanisms, file system permissions, and input sanitization of MDRAP v5.0, evaluating:
- `src/mdrap/security.py`
- `src/mdrap/api.py`
- `src/mdrap/shm.py`
- `src/mdrap/ingestlog.py`

---

## 2. Security Findings

### Finding SEC-01: Shared Memory File Permissions (`0600`)
- **Classification**: `[SOURCE-VERIFIED]` `[TEST-EXECUTED]`
- **Source Location**: `src/mdrap/shm.py:235`
- **Specification**: In multi-tenant co-located environments, shared memory segments must be restricted to the owner process UID (`0600`), preventing unauthorized local users from sniffing market data feeds.
- **Finding**: On POSIX systems, `SHMWriter` executes `os.chmod(shm_path, 0o600)` immediately upon creation. On Windows, file mappings inherit the process security descriptor.
- **Audit Execution**: Tested via `tests/test_security.py::test_shm_permissions_restricted` (passed in Release Gate 07).
- **Verdict**: **COMPLIANT**.

### Finding SEC-02: Cryptographic API Key Hashing & Salt
- **Classification**: `[SOURCE-VERIFIED]` `[TEST-EXECUTED]`
- **Source Location**: `src/mdrap/security.py:hash_api_key`
- **Specification**: API keys stored in configuration files or databases must be hashed using PBKDF2-HMAC-SHA256 or Argon2 with cryptographic salt; plain-text API keys must never be stored.
- **Finding**: API keys are hashed with `hashlib.pbkdf2_hmac("sha256", key.encode(), salt, iterations=100_000)` and verified in constant time via `hmac.compare_digest()`, preventing timing attacks.
- **Verdict**: **COMPLIANT**.

### Finding SEC-03: Zero Raw Secrets in Exception Handlers and Logs
- **Classification**: `[SOURCE-VERIFIED]` `[TEST-EXECUTED]`
- **Source Location**: `src/mdrap/api.py:120`, `src/mdrap/security.py:45`
- **Finding**: All logging statements mask sensitive credentials and token headers (`Authorization: Bearer ***`). Exception tracebacks suppress credential string variables.
- **Audit Execution**: Tested via `tests/test_security.py::test_no_raw_secrets_in_logs`.
- **Verdict**: **COMPLIANT**.

### Finding SEC-04: Bounded Ingress Payloads & Buffer Sanitization
- **Classification**: `[SOURCE-VERIFIED]` `[TEST-EXECUTED]`
- **Source Location**: `src/mdrap/engine.py:53`, `src/mdrap/engine.py:1680`
- **Specification**: Large malformed JSON payloads must be rejected before triggering memory exhaustion or CPU starvation.
- **Finding**:
  - `Engine` enforces `max_payload_bytes = 1,048,576` (1.0 MB).
  - Payloads exceeding `max_payload_bytes` are rejected immediately at `_stage_schema_validation` with `SCHEMA_VIOLATION` without allocating internal buffers.
- **Verdict**: **COMPLIANT**.
