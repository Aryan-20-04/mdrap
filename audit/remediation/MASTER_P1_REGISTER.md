# MDRAP 6.0.0 — MASTER P1 DEFECTS REGISTER

**Baseline Date**: 2026-11-12  
**Baseline Commit**: `01fe87f08c09169305153f817b83c3c9fd8e9118`  
**Remediation Branch**: `remediation/v6.0.0-p1-hardening`  

---

### P1 Findings Catalog

| Finding ID | Title | Primary Affected Files | Owning Agent | Batch | Disposition Status |
| :--- | :--- | :--- | :---: | :---: | :---: |
| **`MDRAP-PY-01`** | Silent Exception Swallowing in Core Modules | `src/mdrap/provenance.py` | Agent 1 | Batch A | PENDING IMPLEMENTATION |
| **`MDRAP-PERS-01`** | Silent Provenance Loss via SQLite Exception Swallowing | `src/mdrap/provenance.py`, `src/mdrap/storage.py` | Agent 1 | Batch A | PENDING IMPLEMENTATION |
| **`MDRAP-TEST-01`** | Failure-Path Testing Blind Spot in Provenance & Persistence | `tests/test_provenance.py` | Agent 1 & Agent 9 | Batch A | PENDING IMPLEMENTATION |
| **`MDRAP-FIN-01`** | Sequence Gap Tracking Keyed by Venue Alone | `src/mdrap/ws_feed.py` | Agent 2 | Batch B | PENDING IMPLEMENTATION |
| **`MDRAP-FIN-02`** | Cross-Venue Reconciliation Blindness to Quote Events | `src/mdrap/reconciliation.py` | Agent 3 | Batch B | PENDING IMPLEMENTATION |
| **`MDRAP-CONC-01`** | Unhandled `queue.Full` Worker Thread Crash | `src/mdrap/live.py`, `src/mdrap/ws_feed.py` | Agent 4 | Batch C | PENDING IMPLEMENTATION |
| **`MDRAP-CONC-02`** | Cross-Thread `QueueFull` Eviction Bypass | `src/mdrap/api.py` | Agent 4 | Batch C | PENDING IMPLEMENTATION |
| **`MDRAP-SEC-01`** | Event Loop DoS via Synchronous Broadcast HMAC Re-Hashing | `src/mdrap/api.py`, `src/mdrap/security.py` | Agent 5 | Batch C | PENDING IMPLEMENTATION |
| **`MDRAP-PERS-02`** | Unsynchronized Concurrent `read_conn` Access | `src/mdrap/storage.py` | Agent 6 | Batch C | PENDING IMPLEMENTATION |
| **`MDRAP-NAT-01`** | Non-Atomic `fastpath_cleanup()` Race Condition | `src/fastpath.c`, `src/mdrap_core.c` | Agent 7 | Batch D | PENDING IMPLEMENTATION |
| **`MDRAP-PKG-01`** | PEP 427 Violation: Bundling Windows DLL in `py3-none-any` Wheel | `pyproject.toml`, `dist/` | Agent 8 | Batch D | PENDING IMPLEMENTATION |
| **`MDRAP-TEST-02`** | Platform-Conditional Skip Concentration Masking Native Fastpath | `tests/` | Agent 8 | Batch D | PENDING IMPLEMENTATION |

*(Note: `MDRAP-PY-01` and `MDRAP-PERS-01` are dual facets of the same persistence exception-swallowing vulnerability; `MDRAP-TEST-01` is the missing negative test layer for this vulnerability; `MDRAP-PKG-01` and `MDRAP-TEST-02` are dual facets of native packaging and CI gating.)*
