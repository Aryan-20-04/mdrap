# MDRAP Pilot Readiness Audit — Agent 2: Incident Management & Deterministic Replay

**Agent Role:** Lead Auditor for Incident Management, State Reconstruction & Forensic Replay  
**Target:** `src/mdrap/incidents.py`, `src/mdrap/replay.py`, `src/mdrap/cli/operations.py`  
**Evaluation Standard:** Deterministic State Reproduction & Microstructure Auditability  
**Date:** 2026-11-11  

---

## 1. Executive Summary

Agent 2 audited the data-quality incident tracking and deterministic replay capabilities introduced in `product-focus/mdrap-reliability-platform`. 

**Core Verification Result:**
The core deterministic replay logic (`IncidentReplayEngine.replay_incident()`) is **VERIFIED**. When executed against persistent historical data, replaying incident `inc-1794334613-0001` or newly synthesized test incidents produces 100% reproducible, bitwise-identical state digests across multiple invocations:
- Replay 1 State Hash: `df34deb48b1770e7d35d81f3a822fbd25379630cf8a9976f5f5887b2dc6d8b79`
- Replay 2 State Hash: `df34deb48b1770e7d35d81f3a822fbd25379630cf8a9976f5f5887b2dc6d8b79`
- Reproducibility Match: `True`

However, two confirmed operational defects were identified that impact pilot readiness:
1. **Unclosed SQLite Connection Handles on Windows** in `IncidentManager`: Leaks open file handles and causes `WinError 32: The process cannot access the file because it is being used by another process` during cleanup and concurrent operations.
2. **CLI Database Routing Mismatch**: Running `python cli.py replay --incident <ID>` attempts to open non-existent `data/mdrap_replay.db` instead of falling back to active platform database `data/mdrap.db`.

---

## 2. Source Code & Architectural Findings

### Finding 2.1: Leaked SQLite Connection Handles in IncidentManager
- **Status:** **CONFIRMED DEFECT (MEDIUM SEVERITY)**
- **File / Lines:** [`src/mdrap/incidents.py:63, 172, 230, 280`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/mdrap/incidents.py#L63)
- **Code:**
  ```python
  def _persist_incident(self, inc: DataQualityIncident) -> None:
      ...
      with sqlite3.connect(self.db_path, timeout=5.0) as conn:
          conn.execute(...)
  ```
- **Evidence:** In Python's standard library `sqlite3`, using `with sqlite3.connect(...) as conn:` manages transaction commit/rollback only. It does **not** close the underlying file descriptor connection. In Windows environments, this leaves the database locked by the process.
- **Impact:** During tests, backup rotations, or temporary directory removals, unhandled `PermissionError` is raised.

### Finding 2.2: CLI Replay Default Database Mismatch
- **Status:** **CONFIRMED DEFECT (LOW/MEDIUM SEVERITY)**
- **File / Lines:** [`src/mdrap/cli/__init__.py:763`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/mdrap/cli/__init__.py#L763), [`src/mdrap/cli/operations.py:412`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/mdrap/cli/operations.py#L412)
- **Code:**
  In `cli/__init__.py`:
  ```python
  p_replay = _sub("replay", cmd_replay, default_db="data/mdrap_replay.db", ...)
  ```
  In `cli/operations.py`:
  ```python
  db_path = getattr(args, "db", None) or from_sqlite or "data/mdrap.db"
  ```
- **Evidence:** Because `default_db` is set to `"data/mdrap_replay.db"`, `getattr(args, "db", None)` is never empty, causing `mdrap replay --incident <ID>` to attempt opening `"data/mdrap_replay.db"`. If the platform database is `"data/mdrap.db"`, the command immediately aborts with:
  `✖ Incident Replay Error: Incident '<ID>' not found in database data/mdrap_replay.db`
  The user is forced to pass an undocumented `--db data/mdrap.db` argument.

### Finding 2.3: Replay Time-Corridor Buffer Window
- **Status:** **VERIFIED WITH DOCUMENTED LIMITATION (LOW SEVERITY)**
- **File / Lines:** [`src/mdrap/replay.py:694-699`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/mdrap/replay.py#L694-L699)
- **Observation:** `IncidentReplayEngine` loads events within a `5.0s` window buffer around `start_timestamp` and `end_timestamp`. This is highly efficient for fast quote bursts, but if an anomaly spans extended disconnects (>5s), events occurring earlier in the sequence may be omitted.
- **Recommendation:** Allow `buffer_s: float = 5.0` to be optionally passed to `replay_incident()`.

---

## 3. Required Remediation for Pilot Readiness

1. **Explicit Connection Teardown in `IncidentManager`**:
   Add `try ... finally: conn.close()` to `_init_db`, `_persist_incident`, `query_incidents`, and `get_incident` in `src/mdrap/incidents.py`.
2. **Graceful CLI DB Fallback**:
   In `src/mdrap/cli/operations.py:412`, if `db_path == "data/mdrap_replay.db"` and does not exist on disk, automatically check for `"data/mdrap.db"`.
