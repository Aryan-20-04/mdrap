# MDRAP 6.0.0 — Redundancy & Duplicate Logic Register

**Audit Date:** November 2026  
**Auditor:** Principal Software Architect  
**Scope:** Functional Duplication, Redundant Logic, and Parallel Implementations  

---

## 1. Executive Summary

This register catalogs all functional duplication and parallel logic identified in MDRAP 6.0.0. Redundant implementations increase maintenance cost, create code drift, and increase the likelihood that bug fixes applied in one location fail to reach its twin.

---

## 2. Master Redundancy Register

| ID | Category | Primary Implementation | Redundant / Parallel Implementation | Architectural Impact | Remediation Recommendation |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **RED-01** | Native C | `src/mdrap/fastpath.c` | `src/mdrap/_fastpath_c.c` & `src/_fastpath_c.c` | Triplicate C code; risks compiling obsolete version. | Delete `_fastpath_c.c` from both locations. |
| **RED-02** | Feeds | `src/mdrap/ws_feed.py:SequenceScope` | `src/mdrap/ws_feed.py:WebSocketFeedManager._last_seq` | Dual sequence tracking; production manager ignores scoped tracking. | Delete flat dictionary in `WebSocketFeedManager`; route through `SequenceScope`. |
| **RED-03** | Validation | `src/mdrap/reconciliation.py:reconcile_quote` | `src/mdrap/quality.py:evaluate` | Both independently check for crossed books (`bid >= ask`). | Keep quality engine as canonical gate; let reconciler consume quality status. |
| **RED-04** | Quarantine | `src/mdrap/storage.py:write_quarantine_batch`| `src/mdrap/quarantine.py:QuarantineManager` | Dual quarantine tables and memory buffers. | Delete `quarantine.py`; route all quarantine events through `Store`. |
| **RED-05** | Packaging | `pyproject.toml` | `compat/pyproject.toml` & `contrib/pyproject.toml` | Triplicate project metadata definitions. | Centralize build configuration or generate subpackage metadata dynamically. |

---

## 3. Redundancy Remediation Priority

1. **Eliminate C File Duplication (RED-01):** High priority to avoid binary compilation confusion.
2. **Unify Sequence Tracking (RED-02):** High priority to eliminate WebSocket feed sequence collisions.
3. **Purge Legacy Quarantine Manager (RED-04):** Medium priority to remove deprecated classes.
