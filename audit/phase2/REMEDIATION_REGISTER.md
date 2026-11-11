# Phase 2 Operational Remediation Register

**Target Branch**: `product-focus/mdrap-reliability-platform`  
**Execution Phase**: Phase 2 — Real-World Design Partner Pilot & Operational Validation  
**Date**: 2026-11-11  
**Status**: All Identified Defects Resolved & Verified  

---

## 1. Remediation Register Matrix

| Defect ID | Severity | Subsystem | Description & Root Cause | Affected Files | Remediation Action | Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **DEF-05** | **MEDIUM** | Replay Engine | `replay_incident` was hardcoded to a fixed $\pm 5.0$ second time corridor, preventing wider forensic investigations (30s, 60s, 300s) during extended market disruptions. | `src/mdrap/replay.py` | Added configurable `buffer_s: float = 5.0` parameter to `replay_incident(self, incident_id: str, buffer_s: float = 5.0)` and passed corridor into SQL window filter. | **RESOLVED & VERIFIED** |
| **DEF-06** | **HIGH** | Storage / Concurrency | `Store.write_provenance_batch()` executed `cursor.executemany()` without an immediate `self.conn.commit()`, leaving an active transaction open on `Store.conn`. Under multi-component access, subsequent SQLite connections encountered `sqlite3.OperationalError: database is locked`. | `src/mdrap/storage.py` | Added explicit `self.conn.commit()` immediately following `executemany()` inside `write_provenance_batch()`. | **RESOLVED & VERIFIED** |
| **DEF-07** | **LOW** | External Verification | Standalone verification of Merkle tree proofs and wire digests previously depended on internal MDRAP package imports, complicating zero-trust customer verification. | `tools/verify_evidence.py` | Built dedicated standalone verifier relying solely on Python standard library (`hashlib`, `hmac`, `json`). | **RESOLVED & VERIFIED** |
| **DEF-08** | **LOW** | Security / Audit Surface | Absence of automated static AST audit script to verify zero order-routing APIs across repository updates. | `tools/check_read_only_safety.py` | Implemented AST-based static analysis tool verifying 0 forbidden order methods across all 125 source files. | **RESOLVED & VERIFIED** |

---

## 2. Verification Protocol & Execution Evidence

1. **DEF-05 Verification**:
   - *Test*: `tests/test_phase2_replay.py::test_dynamic_corridor_expansion`
   - *Result*: Evaluated 5s, 30s, and 120s buffer windows around synthetic and live incidents; event capture sets expanded proportionally and deterministically.
2. **DEF-06 Verification**:
   - *Test*: `tests/test_pilot_readiness.py` & `tests/test_phase2_cryptography.py`
   - *Result*: Verified that multi-process and multi-connection SQLite writes cleanly commit without locking or `WinError 32` file locking errors on Windows.
3. **DEF-07 Verification**:
   - *Test*: `tests/test_phase2_cryptography.py::test_external_standalone_verifier_success`
   - *Result*: Verified zero-dependency verification script correctly validates SHA-256 wire digests, lineage hashes, and RFC 6962 Merkle inclusion proofs.
4. **DEF-08 Verification**:
   - *Test*: `tests/test_phase2_security_profile.py::test_static_ast_safety_audit`
   - *Result*: Scanned 125 files in `src/mdrap`; confirmed 0 order routing methods or secrets.
