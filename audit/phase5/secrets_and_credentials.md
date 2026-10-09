# Phase 5 Secrets Management & Credential Isolation

**Date**: 2026-10-09  
**Status**: ZERO HARDCODED SECRETS / AUTOMATED REDACTION ENFORCED  

---

## 1. Secrets Handling Rules

1. **Zero Secret Persistence**:
   - Plaintext API tokens are returned once upon registration (`register_api_key`) and never stored in files, memory tables, or databases.
   - Only HMAC-SHA256 digests (`token_hash`) are persisted.
2. **Environment Variable Injection**:
   - Master key derivation salt (`MDRAP_API_KEY_SALT`) must be injected via protected systemd environment or secure vault.
   - Missing salt halts startup immediately in production mode.
3. **Automated Diagnostic Redaction**:
   - `scripts/diagnostic_bundle.py` recursively inspects all configuration keys and replaces sensitive values (`token`, `salt`, `secret`, `password`, `key`) with `[REDACTED]`.

---

## 2. Credential Rotation & Revocation Runbook

1. **Generate New Key**:
   ```bash
   python -m mdrap.cli security create-key --client-id Desk_Alpha --role OPERATOR
   ```
2. **Revoke Compromised Key**:
   ```bash
   python -m mdrap.cli security revoke-key --key-id <16_hex_key_id>
   ```
   Revocation takes effect immediately in memory and is committed to SQLite backing store.
