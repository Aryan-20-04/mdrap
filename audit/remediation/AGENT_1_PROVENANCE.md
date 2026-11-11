# MDRAP 6.0.0 — REMEDIATION REPORT: AGENT 1

## WORKSTREAM: PROVENANCE PERSISTENCE & FAILURE SEMANTICS
**Findings Remediated**: `MDRAP-PY-01`, `MDRAP-PERS-01`, `MDRAP-TEST-01`  
**Engineer / Lead**: Agent 1 — Provenance & Persistence Specialist  
**Date**: 2026-11-12  
**Branch**: `remediation/v6.0.0-p1-hardening`  
**Status**: **VERIFIED FIXED**

---

### 1. Original Defect Description
In `src/mdrap/provenance.py`, `EventProvenanceTracker.record()` contained `except Exception: pass` blocks around both storage backend delegation (`self.store.write_provenance_batch([prov])`) and direct SQLite persistence.
When SQLite encountered lock contention, database timeouts, disk-full conditions, or schema constraint failures:
- Persistence failed silently.
- No error or warning was logged.
- No failure or drop counters were incremented.
- No dead-letter or retry queue captured the uncommitted lineage.
- Once the in-memory window rolled over, forensic evidence was permanently lost.
Furthermore, `export_evidence()` reported `verification_status="VERIFIED_TAMPER_EVIDENT"` even for records that had failed disk persistence.

---

### 2. Exact Root Cause
Over-defensive swallowing of exceptions intended to keep the hotpath running, combined with the lack of:
1. Explicit durability states (`PENDING`, `DURABLE_DISK`, `FAILED_RECOVERABLE`, `FAILED_UNRECOVERABLE`).
2. Error categorization (transient locks vs permanent constraint/filesystem errors).
3. Thread-safe operational drop and retry counters.
4. A dead-letter retry buffer (`_pending_retry`).
5. Negative-path test fixtures simulating database locks and storage failures.

---

### 3. Implementation Details & Files Changed

#### Modified: [`src/mdrap/provenance.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/mdrap/provenance.py)
1. **Explicit Durability States**:
   Introduced `ProvenanceDurabilityState(str, Enum)` with states:
   - `PENDING = "PENDING"`
   - `DURABLE = "DURABLE_DISK"`
   - `FAILED_RECOVERABLE = "FAILED_RECOVERABLE"`
   - `FAILED_UNRECOVERABLE = "FAILED_UNRECOVERABLE"`
2. **Observable Operational Counters**:
   Added thread-safe counters to `ProvenanceTracker`:
   - `self.recorded_count: int = 0`
   - `self.persisted_count: int = 0`
   - `self.failed_recoverable_count: int = 0`
   - `self.failed_unrecoverable_count: int = 0`
   - `self.recovered_count: int = 0`
   - `self.dropped_provenance_count: int = 0`
3. **Transient Lock Dead-Letter Retry Buffer**:
   Added `self._pending_retry: collections.deque[EventProvenance] = collections.deque(maxlen=10_000)` and `flush_pending_retries() -> int`.
4. **Structured Error Handling**:
   Implemented `_handle_persistence_failure(prov, exc)`:
   - Detects recoverable `sqlite3.OperationalError` (locked, busy, timeout). Logs `WARNING`, sets `FAILED_RECOVERABLE`, enqueues to `_pending_retry`.
   - Detects unrecoverable errors (readonly, constraint, corruption). Logs `ERROR`, increments `dropped_provenance_count` and `failed_unrecoverable_count`, sets `FAILED_UNRECOVERABLE`.
5. **Evidence Degradation Indicator**:
   Updated `export_evidence()`:
   - Sets `verification_status = f"DEGRADED_{prov.durability_status}"` whenever an event is in `FAILED_*` or `PENDING` status.
   - Includes `"persistence_state": prov.durability_status` in evidence payload.
6. **$O(1)$ Memory Rollover**:
   Replaced $O(N)$ `self._order.pop(0)` list shifts with `collections.deque.popleft()`.

---

### 4. Regression & Fault-Injection Tests

#### Created: [`tests/test_provenance.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/tests/test_provenance.py)
1. `test_provenance_happy_path_memory_and_store`:
   - Validates recording to in-memory store (`COMMITTED_MEM`) and disk-backed store (`DURABLE_DISK`).
   - Verifies `persisted_count == 1`, zero drops, and tamper-evident evidence export.
2. `test_provenance_fault_injection_transient_database_lock`:
   - Injects `sqlite3.OperationalError("database is locked")`.
   - Asserts `failed_recoverable_count == 1`, `prov.durability_status == "FAILED_RECOVERABLE"`, `_pending_retry` has 1 item.
   - Asserts `export_evidence()` reports `DEGRADED_FAILED_RECOVERABLE`.
   - Simulates lock clearing and calls `flush_pending_retries()`; verifies `recovered_count == 1` and evidence restores to `VERIFIED_TAMPER_EVIDENT`.
3. `test_provenance_fault_injection_unrecoverable_error`:
   - Injects fatal `sqlite3.IntegrityError`.
   - Asserts `failed_unrecoverable_count == 1`, `dropped_provenance_count == 1`, evidence reports `DEGRADED_FAILED_UNRECOVERABLE`.
4. `test_provenance_sqlite_direct_recovery_after_restart`:
   - Writes records to a SQLite database, terminates tracker, instantiates a brand new tracker on the same database file, and verifies authoritative recovery via `get()`.
5. `test_provenance_concurrent_recording_thread_safety`:
   - Spawns 8 concurrent threads recording 800 events; asserts 100% thread-safe counter accuracy and zero race conditions.

---

### 5. Execution Verification Evidence
```
platform win32 -- Python 3.13.1, pytest-9.2.0, pluggy-1.6.0
collected 5 items

tests/test_provenance.py::test_provenance_happy_path_memory_and_store PASSED [ 20%]
tests/test_provenance.py::test_provenance_fault_injection_transient_database_lock PASSED [ 40%]
tests/test_provenance.py::test_provenance_fault_injection_unrecoverable_error PASSED [ 60%]
tests/test_provenance.py::test_provenance_sqlite_direct_recovery_after_restart PASSED [ 80%]
tests/test_provenance.py::test_provenance_concurrent_recording_thread_safety PASSED [100%]

======================== 5 passed, 1 warning in 1.52s =========================
```

---

### 6. Remaining Limitations
The dead-letter buffer (`_pending_retry`) is currently in-memory with a capacity of 10,000 items. If the process terminates while SQLite remains permanently locked, unrecovered records in `_pending_retry` are lost from SQLite (though they remain logged in structured warnings). Stage 3 will add an optional disk-backed overflow spill file for `_pending_retry`.
