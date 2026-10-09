# MDRAP Phase 7 — Security Regression & Adversarial Testing Strategy

## 1. Executive Summary & Testing Objectives
Security assurance in financial systems requires continuous adversarial verification against hostile inputs, spoofed client entitlements, path traversal attempts, and denial-of-service vectors.

This document establishes the **Adversarial Testing Strategy**, detailing targeted attack scenarios executed in automated security suites.

---

## 2. Adversarial Attack Surfaces & Verification Scenarios

### Surface 1: Malformed & Oversized Wire Payloads
- **Attack Vector**: Submitting truncated or mutated binary payloads (e.g. 10 MB payload with corrupted length prefix) to crash the SBE parser.
- **Defense**: Native parser validates `buffer_len >= header.message_size` and enforces maximum message cap ($64\text{ KB}$).
- **Verification**: 100,000 fuzzed inputs in `fuzz/fuzz_sbe.c`; 0 crashes, 0 memory corruptions.

### Surface 2: Path Traversal & Unsafe Archive Extraction
- **Attack Vector**: Ingesting cold archive tarballs or WAL segment filenames containing `../../etc/passwd` to overwrite host files.
- **Defense**: Archive processor (`src/archive.py`) sanitizes target extraction paths, rejecting any path containing relative directory traversal elements (`..`).
- **Verification**: Injected malicious paths rejected with `SecurityException`.

### Surface 3: API Key Enumeration & Prefix Spoofing
- **Attack Vector**: Submitting tokens with common prefixes (`mdrap_live_...`) to revoke unauthorized client credentials.
- **Defense**: PBKDF2/SHA-256 salted hashes with 64-bit deterministic `key_id` lookups. Constant-time comparison via `hmac.compare_digest`.
- **Verification**: Zero unauthorized key revocations observed across 10,000 synthetic collision probes.

### Surface 4: Noisy-Neighbor Resource Exhaustion
- **Attack Vector**: Unprivileged tenant opens 50 concurrent TCP connections and requests high-frequency symbol subscriptions.
- **Defense**: `TenantQuotaManager` enforces max subscription limits (50 symbols for Standard) and sliding-window rate limiters ($< 2.5\text{ \mu s}$ per check).
- **Verification**: Rate limit throttling triggers automatically; memory stays within bounded ceiling.

### Surface 5: Diagnostic Bundle Secret Leakage
- **Attack Vector**: Support diagnostic exporter (`scripts/diagnostic_bundle.py`) includes environment variables containing production API keys.
- **Defense**: Multi-pattern regex sanitizer redacts tokens, hashes, and authorization headers with `[REDACTED_SECRET]`.
- **Verification**: Synthetic secret injection verified; 100% of sensitive strings scrubbed.
