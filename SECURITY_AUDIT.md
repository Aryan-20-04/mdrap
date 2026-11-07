# MDRAP — Comprehensive Security Audit & Threat Model

**Audit Type**: Institutional Cybersecurity Vulnerability Assessment & Remediation  
**Target**: MDRAP Financial Market Data Infrastructure  
**Classification**: High-Frequency Trading (HFT) Perimeter & Core Engine Defense  
**Standard**: OWASP Top 10, CWE / SANS Top 25, Institutional Defense-in-Depth  

---

## 1. Vulnerability Findings & Risk Matrix

| Finding ID | Threat / Vulnerability | OWASP / CWE | Severity | Exploitability | Remediation Status |
|---|---|---|---|---|---|
| **SEC-01** | Unauthenticated Silent Mock-Data Fallback in Live Feeds | CWE-392 (Failure to Report Error Condition) | **HIGH** | **HIGH** | **REMEDIATED** |
| **SEC-02** | Demo Mode (`MDRAP_DEMO=1`) Weaponization Risk in Production | CWE-798 (Use of Hard-coded Credentials) | **HIGH** | **MEDIUM** | **REMEDIATED** |
| **SEC-03** | Insecure Network Binding Defaults (`0.0.0.0`) | CWE-1327 (Binding to Insecure Address) | **MEDIUM** | **HIGH** | **REMEDIATED** |
| **SEC-04** | Implausible Future Timestamps Bypass Quality Rules | CWE-20 (Improper Input Validation) | **MEDIUM** | **MEDIUM** | **REMEDIATED** |
| **SEC-05** | Potential C-API Null Pointer Dereference in `py_eval_fast2` | CWE-476 (NULL Pointer Dereference) | **LOW** | **LOW** | **REMEDIATED** |
| **SEC-06** | Memory Allocation Failure in C-API Depth Builder | CWE-401 (Improper Resource Release) | **LOW** | **LOW** | **REMEDIATED** |

---

## 2. Deep Dive Vulnerability Analysis & Proof of Remediation

### SEC-01: Silent Mock-Data Substitution on Missing Live Credentials

- **Severity**: **HIGH** (CVSS 7.4)
- **Description**:
  When an operator executed live market data streaming (`mdrap live --feed polygon` or `PolygonFeedManager(...)`) without supplying an API key, the feed adapter silently switched into mock simulation mode (`mock_mode = True`). The operator received fabricated, synthetic ticks while operating under the belief that live market data was streaming.
- **Impact**:
  Trading algorithms could execute real capital orders against synthetic mock price feeds, leading to catastrophic mispricing or erroneous risk calculations.
- **Remediation**:
  Removed all implicit mock-mode assignments. If `api_key` is not configured and `mock_mode` is False, the constructor raises an explicit `ValueError`. Live CLI execution aborts with a clear error unless the operator explicitly passes `--mock-feed`.
- **Verification**: Verified via `tests/test_polygon_feed.py` and `tests/test_databento_feed.py`.

---

### SEC-02: Hardened Demo Credentials & Production Environment Isolation

- **Severity**: **HIGH** (CVSS 7.1)
- **Description**:
  The repository contains known demo credentials (`mdrap_demo_key` with `Role.ADMIN`) and static salt (`mdrap_demo_salt_v1`) to facilitate local offline sandbox testing. If an operator inadvertently deployed a production instance with `MDRAP_DEMO=1`, an attacker could gain administrative privileges using public demo keys.
- **Remediation**:
  `SecurityManager.__init__` and `hash_api_key()` enforce environment-aware fail-closed validation. If `MDRAP_ENV` or `ENVIRONMENT` is set to `production` or `MDRAP_PRODUCTION=1`, attempting to enable `MDRAP_DEMO=1` raises `AccessDenied` immediately. Demo keys and static demo salt cannot be initialized under production configuration under any circumstances.
- **Verification**: Tested in `tests/test_hardening_s1.py` and security test suites.

---

### SEC-03: Local Loopback Binding by Default (`127.0.0.1`)

- **Severity**: **MEDIUM** (CVSS 5.3)
- **Description**:
  `mdrap serve` and CLI operation handlers previously defaulted to `host="0.0.0.0"`. Starting the service exposed REST endpoints, WebSocket streams, and Prometheus metrics to the public network adapter if the host lacked external firewall protection.
- **Remediation**:
  Changed default bind address across all CLI commands to `127.0.0.1`. Binding to public or external interfaces requires explicit configuration via `--host 0.0.0.0` or `MDRAP_HOST=0.0.0.0`.

---

### SEC-04: Prevention of Future Timestamp Watermark Poisoning

- **Severity**: **MEDIUM** (CVSS 5.0)
- **Description**:
  When unnormalized timestamps (nanoseconds or milliseconds) or malicious far-future timestamps were ingested, `Engine.step()` did not flag future timestamps exceeding future clock skew limits (`max_future_skew_s`).
- **Remediation**:
  Integrated `normalize_timestamp_seconds()` in the gateway to auto-scale epoch values, and added `Reason.TS_IMPLAUSIBLE` validation in `Engine._stage_quote_and_staleness`. Events with timestamps exceeding future skew tolerance are flagged `SUSPICIOUS` and prevented from advancing watermarks.

---

### SEC-05 & SEC-06: C Extension Pointer & Memory Safety

- **Severity**: **LOW** (CVSS 3.3)
- **Description**:
  In `src/mdrap/_fastpath_c.c`, `py_eval_fast2` cast the raw pointer argument directly without verifying that `eng` was non-null. In `py_shm_read_slot_v3`, `Py_BuildValue` results for book depth items were passed to `PyList_SET_ITEM` without checking for allocation failure.
- **Remediation**:
  Added explicit NULL pointer checks and allocation guards. If `eng` is NULL, `py_eval_fast2` sets `PyExc_ValueError` and returns NULL safely. If `Py_BuildValue` fails, resources are cleaned up via `Py_DECREF` and memory exhaustion exceptions are propagated safely.

---

## 3. Defense-in-Depth Security Controls

1. **Role-Based Access Control (RBAC)**:
   - `VIEWER`: Read-only access to BBO, depth ladders, historical candles, and health status.
   - `OPERATOR`: Ingestion controls, feed management, and quarantine inspection.
   - `ADMIN`: Source blocking, secrets rotation, chaos injection, and audit review.
2. **Rate Limiting**:
   - Token bucket rate limiter defending against quote flooding and Denial of Service (DoS).
   - High-throughput capacity (20,000 req/sec) with strict eviction rules protecting depleted buckets.
3. **Cryptographic Audit Trail**:
   - Merkle-tree structured tamper-evident audit logs with domain separation prefixes (`0x00` leaf, `0x01` node).
   - HMAC-SHA256 signature verification over incoming feed frames with constant-time equality checks.
4. **Shared Memory Permissions**:
   - Memory mapped segments created with strict `0600` permissions (owner-only read/write).

---

## 4. Remaining Security Risks & Recommended Controls

- **Network Perimeter**: When deploying MDRAP across distributed hosts, always place the API server behind an authenticated reverse proxy (e.g. NGINX, Envoy) with TLS 1.3 termination and mTLS.
- **Secrets Management**: Store API salts and HMAC feed secrets in an enterprise secret manager (e.g. HashiCorp Vault, AWS Secrets Manager) and inject them via `MDRAP_SECRET_*` environment variables.
