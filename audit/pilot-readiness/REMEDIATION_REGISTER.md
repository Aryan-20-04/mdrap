# MDRAP Pilot Readiness Audit — Phase 4: Remediation Register

**Target:** `product-focus/mdrap-reliability-platform`  
**Date:** 2026-11-11  
**Status:** In Progress / Controlled Remediation  

---

## 1. Remediation Register Matrix

| Defect ID | Severity | Subsystem | Description & Root Cause | File Target | Remediation Action | Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **DEF-01** | **HIGH** | Provenance | `ProvenanceTracker` stored records in memory only. On restart, queries returned `None` or degraded records without `payload_sha256` or `channel`. | `src/mdrap/provenance.py`<br>`src/mdrap/storage_schema.py`<br>`src/mdrap/storage.py` | Add `event_provenance` table schema. Implement SQLite persistence in `ProvenanceTracker.record()` and lossless retrieval in `ProvenanceTracker.get()`. | **RESOLVED & VERIFIED** |
| **DEF-02** | **MEDIUM** | Incidents | `IncidentManager` used `with sqlite3.connect(...)` without `conn.close()`, causing leaked SQLite file handles and Windows `WinError 32` errors during test cleanup. | `src/mdrap/incidents.py` | Wrap all database queries in `try ... finally: conn.close()` to guarantee deterministic connection cleanup; support shared `Store` connection. | **RESOLVED & VERIFIED** |
| **DEF-03** | **MEDIUM** | CLI / Replay | `cmd_replay` failed when `--incident` was invoked without `--db` because parser default was `"data/mdrap_replay.db"` while system DB was `"data/mdrap.db"`. | `src/mdrap/cli/operations.py` | Implement intelligent database fallback: check `"data/mdrap.db"` if default replay database does not exist or lacks the incident. | **RESOLVED & VERIFIED** |
| **DEF-04** | **LOW** | Connectors | `WebSocketFeedManager.get_provider_telemetry()` hardcoded a universal `10_000.0` ms threshold for `STALE` status. | `src/mdrap/ws_feed.py` | Add configurable `stale_timeout_ms: float = 10_000.0` to constructor and enforce it in telemetry evaluation. | **RESOLVED & VERIFIED** |

---

## 2. Verification Protocol for Each Fix
1. **DEF-01 Verification:** Write test in `tests/test_pilot_readiness.py` storing provenance into a persistent disk SQLite DB, closing the process/store, instantiating a brand-new `ProvenanceTracker` from the disk DB, and asserting that `payload_sha256`, `lineage_hash`, and all provenance fields match byte-for-byte.
2. **DEF-02 Verification:** Run incident lifecycle tests on Windows within `tempfile.TemporaryDirectory()`; verify temporary directory cleanup succeeds with zero `PermissionError`.
3. **DEF-03 Verification:** Run `python cli.py replay --incident <ID>` without passing `--db`, verify that it automatically finds the incident in `data/mdrap.db` and renders the timeline.
4. **DEF-04 Verification:** Initialize `WebSocketFeedManager` with custom `stale_timeout_ms=500.0`, simulate 600ms pause, verify `ConnectorState.STALE`.
