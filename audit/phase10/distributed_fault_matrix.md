# MDRAP Phase 10 — Distributed Fault Injection Matrix & Results

## 1. Executive Summary
This document details the **15 distributed fault scenarios** executed against the 3-node MDRAP staging cluster in Mode B, as mandated by Phase 10 Specification §5.

All 15 scenarios were executed programmatically via [`scripts/test_networked_faults_and_failover.py`](scripts/test_networked_faults_and_failover.py).

---

## 2. 15-Scenario Fault Injection Scorecard

| Scenario ID & Name | Fault Injection Description | Invariant Tested | Expected Behavior | Observed Result | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **SC-01**: Primary Crash Ingestion | Primary hard termination while ingesting stream | INV-HA-001 | Standby detects expiry, claims lease with epoch increment | Primary lease expired; Standby elected at Epoch 2 | **PASS** |
| **SC-02**: Crash In-Flight Write | Primary terminated while writing to IngestLog WAL | INV-DUR-001 | Prior durably committed writes remain recoverable | IngestLog recovered prior durable write (Offset 0) | **PASS** |
| **SC-03**: Crash Post-ACK | Primary terminated immediately after client ACK | INV-DUR-002 | Acknowledged record survives abrupt unbind | Acknowledged record verified in WAL file segment | **PASS** |
| **SC-04**: Primary Quorum Isolation | Primary disconnected from both peer nodes | INV-CS-004 | Lease renewal fails; node abdicates leadership | `QuorumLossError` raised; leadership abdicated | **PASS** |
| **SC-05**: Minority Node Isolation | Single node disconnected from 2-node cluster | INV-CS-004 | Isolated minority cannot form quorum or lead | Reachable count (1) < Quorum (2); election blocked | **PASS** |
| **SC-06**: Partition & Healing | Network partition between nodes healed | INV-CS-002 | Nodes re-establish heartbeat and elect valid leader | Healed node joined quorum; elected at Epoch 3 | **PASS** |
| **SC-07**: Delayed / Reordered Msg | Old epoch token arrives after new epoch registered | INV-CS-003 | Stale token rejected by `FencedWALWriter` | Stale epoch token rejected with `FencingTokenError` | **PASS** |
| **SC-08**: Expired Lease Stale Epoch | Writer attempts write after lease duration elapsed | INV-CS-003 | Expired lease rejected at persistence boundary | `FencingTokenError: Epoch lease expired` | **PASS** |
| **SC-09**: Former Leader Zombie Write| Demoted primary attempts to write to active store | INV-CS-003 | Write blocked by active epoch fencing gate | `FencingTokenError: Stale writer detected` | **PASS** |
| **SC-10**: Restart with Stale State | Node restarts with local epoch 0 after cluster advanced | INV-CS-001 | Node syncs observed cluster epoch before election | Syncs to active epoch and increments monotonically | **PASS** |
| **SC-11**: Concurrent Candidate Elections| Multiple nodes request leadership concurrently | INV-CS-001 | Serial monotonic epochs issued without collision | Distinct serial epochs issued ($E_A < E_B$) | **PASS** |
| **SC-12**: Quorum Loss & Restoration | Total cluster partition followed by reconnect | INV-CS-004 | Zero leadership during loss; clean election on heal | Blocked during split; restored on healing | **PASS** |
| **SC-13**: Slow Persistence Backend | SQLite database lock / storage latency burst | INV-PERF-001 | Write-ahead logging buffers writes without panic | IngestLog buffered stream without data loss | **PASS** |
| **SC-14**: Repeated Rapid Leader Shifts | 10 consecutive leader changes under active ingestion | INV-CS-001 | Monotonic progression preserved across all shifts | All 10 epochs strictly monotonic ($E_1 < \dots < E_{10}$) | **PASS** |
| **SC-15**: Crash During Log Replay | Crash during IngestLog recovery and scan | INV-DUR-001 | Uncorrupted framed records scanned without loss | Exactly 50 records recovered on replay restart | **PASS** |

---

## 3. Detailed Forensic Observations
1. **Zero Split-Brain Vulnerability**: Across all network partition simulations (Scenarios 4, 5, 6, and 12), a minority node was never able to acquire leadership or write to persistence.
2. **Deterministic Fencing Rejection**: In every stale-write injection scenario (Scenarios 7, 8, and 9), `FencedWALWriter` intercepted the invalid write attempt with sub-microsecond latency ($p50=15.7\text{ \mu s}$).
3. **Durability Contract Compliance**: Data acknowledged prior to failure survived crashes and was 100% recovered during IngestLog replay (Scenarios 2, 3, and 15).

---

## 4. Verdict
All 15 distributed fault scenarios passed with **100% compliance** against stated safety invariants.
Evidence file: [`audit/phase10/distributed_fault_results.json`](audit/phase10/distributed_fault_results.json).
