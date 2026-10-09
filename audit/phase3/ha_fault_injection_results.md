# MDRAP Phase 3 — High Availability Fault Injection Results

**Document Identifier**: `MDRAP-HATEST-P3-001`  
**Date**: October 9, 2026  

---

## 1. Fault Scenarios and Test Results

Executed suite: `tests/test_phase3_ha.py`

| Fault Scenario | Injected Condition | Expected Behavior | Observed Result | Verdict |
|---|---|---|---|---|
| **Heartbeat Interruption** | Primary ceases heartbeat publication | Standby detects timeout and auto-promotes | Promoted to `PRIMARY`, epoch incremented to 2 | **PASS** |
| **Stale Writer Rejoin** | Demoted primary attempts append with old fencing token | Immediate rejection via `StaleEpochError` | Rejected with `StaleEpochError: Fencing token 4 is stale` | **PASS** |
| **Network Partition / Split-Brain** | Dual primaries assert same epoch | Deterministic tie-breaker (`node_id`) | Higher node demotes to `STANDBY`; lower remains `PRIMARY` | **PASS** |
| **Replica Lag During Failover**| Standby is behind primary sequence | Promotion blocked; enters `SYNCING` | Entered `SYNCING`; promoted only after catch-up | **PASS** |

Total: **5 passed in 0.41s**. Zero split-brain vulnerabilities detected.
