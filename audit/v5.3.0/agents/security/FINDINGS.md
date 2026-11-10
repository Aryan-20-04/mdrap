# Security & Threat Modeling — Detailed Findings

## Executive Summary
Audited API security, secrets management, cryptographic algorithms, injection risks, SHM permissions, and CI/CD security. MDRAP demonstrates strong foundational cryptography (HMAC-SHA256, salted token hashing, parameterized SQL queries). However, 3 HIGH severity vulnerabilities were identified: `/metrics` auth bypass via IP spoofing, browser WebSocket incompatibility with incomplete revocation checks, and broken RFC 6962 domain separation in `packages/mdrap-audit`.

## Categorized Findings

### HIGH
1. **F-SEC-01: `/metrics` Authentication Bypass via `X-Forwarded-For` Header Spoofing**:
   - `src/mdrap/api.py:1670-1678` exempts loopback clients (`127.0.0.1`, `::1`) from metrics authentication.
   - `src/mdrap/api.py:72-90` resolves client IP by taking the **leftmost** entry of `X-Forwarded-For`.
   - **Impact**: Any external attacker sending `X-Forwarded-For: 127.0.0.1` through a trusted reverse proxy bypasses Prometheus authentication.

2. **F-SEC-02: Browser WebSocket Incompatibility & Incomplete Revocation Enforcement**:
   - `src/mdrap/api.py:1483-1496`: WebSocket `/v1/events/stream` requires auth headers (`Authorization`, `X-API-Key`). Standard browser JavaScript WebSocket API cannot pass custom HTTP headers.
   - `src/mdrap/api.py:368-378`: `broadcast_event` checks `if tok and not ent: dead_sockets.append(ws)`. If `tok` is None or empty, the check is skipped and data is delivered.

3. **F-SEC-03: Broken RFC 6962 Domain Separation in `packages/mdrap-audit`**:
   - `packages/mdrap-audit/mdrap_audit/__init__.py:81-94`: `build()` computes internal parent hashes without prepending the required `0x01` byte prefix.
   - Incompatible with core engine `src/mdrap/security.py:209-213` which correctly uses `0x01` prefix.

### MEDIUM
4. **F-SEC-04: Unauthenticated `/v1/readiness` & `/v1/health` Contend Storage RLock**:
   - `src/mdrap/api.py:714-715`: `/v1/readiness` acquires `st.store._lock` (the process-wide SQLite write lock). Sustained unauthenticated probes induce pipeline latency spikes.

5. **F-SEC-05: POSIX SHM Permissions Race Condition & Platform Incompatibility**:
   - `src/mdrap/shm.py:236-242`: `os.chmod("/dev/shm/...")` runs *after* file creation, leaving a brief TOCTOU window where unprivileged local users can read memory. Fails on macOS and Windows.

6. **F-SEC-06: Plaintext API Key Retained in Process Heap for Newly Registered Keys**:
   - `src/mdrap/security.py:1057-1072`: `register_api_key` stores `ent.token` (plaintext) in `self._api_keys[tok_hash]`, retaining it in heap memory.

7. **F-SEC-07: Outdated Supported Versions in `SECURITY.md`**:
   - `SECURITY.md:5-9` lists only versions 3.0.x and 3.1.x as supported.

### LOW / INFO
8. **F-SEC-08: Wildcard CORS Default in `.env.example`**:
   - `.env.example:24` sets `MDRAP_CORS_ORIGINS=*`.
9. **F-SEC-09: Verified Security Strengths**:
   - 100% parameterized SQL queries (zero SQL injection).
   - `subprocess.run` uses list arguments with `shell=False` (zero command injection).
   - Constant-time `hmac.compare_digest` used across all token comparisons.\n