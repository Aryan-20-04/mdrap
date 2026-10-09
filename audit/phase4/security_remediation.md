# Phase 4 Security Remediation Log

**Status**: ALL IDENTIFIED FINDINGS REMEDIATED  
**Timestamp**: 2026-10-09  

---

## 1. Security Remediation Actions

| Risk / Finding ID | Severity | Root Cause | Remediated Implementation | Verification Test |
| :--- | :--- | :--- | :--- | :--- |
| **SEC-001** | High | Low entropy in key prefix revocation lookup | Introduced 16-hex `key_id` derived directly from SHA-256 hash | `test_token_salt_and_constant_time_verification` |
| **SEC-002** | High | Unsalted API key hashes susceptible to rainbow tables | Mandatory `MDRAP_API_KEY_SALT` HMAC-SHA256 derivation | `test_token_salt_and_constant_time_verification` |
| **SEC-003** | Medium | Potential directory collision on IngestLog | Implemented platform-native exclusive advisory lock file (`.lock`) | `test_ingestlog_directory_safety_and_locking` |
| **SEC-004** | Medium | Overlong symbol names overflowing binary structs | Clamped ASCII encoding to 16 bytes with fixed zero padding | `test_sbe_wire_bounds_and_null_terminator_safety` |
| **SEC-005** | High | Permissive default on client licensing | Enforced fail-closed evaluation in `DurableUsageMeter.verify_entitlement` | `test_fail_closed_entitlement_enforcement` |
