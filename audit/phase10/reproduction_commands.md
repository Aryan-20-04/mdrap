# MDRAP Phase 10 — Step-by-Step Reproduction Guide

## 1. Executive Summary
This document provides the complete, deterministic sequence of shell commands to independently reproduce all **test suites, distributed fault simulations, failover benchmarks, and fan-out measurements** executed during Phase 10.

---

## 2. Environment Setup

All commands are designed for **PowerShell** on Windows or **Bash** on Linux.

```powershell
# 1. Clone or enter MDRAP repository
cd C:\Users\KIIT0001\Desktop\Projects\mdrap

# 2. Configure mandatory staging environment secrets
$env:MDRAP_API_KEY_SALT = "staging_cluster_salt_phase10_secret"
$env:MDRAP_DAEMON_TOKEN = "staging_admin_token_phase10"
$env:PYTHONPATH = "src"
```

---

## 3. Platform Test Suite Execution

To execute the complete platform test suite across all 1,240+ test cases:

```powershell
# Run full regression suite with short traceback
python -m pytest tests/ -v --tb=short
```

---

## 4. Phase 10 Verification Campaigns

### 4.1 Cluster Deployment & Connectivity Validation
Spawns a 3-node cluster, probes TCP sockets, evaluates HEALTH and STATUS endpoints, and initializes quorum consensus:
```powershell
python scripts/deploy_networked_cluster.py
```
*Emits:* `deployment_validation.json`, `network_connectivity_results.json`, `node_inventory.json`

### 4.2 Distributed Faults & 100-Trial Failover Benchmark
Executes 15 distributed failure scenarios and measures 100 consecutive primary crash/failover cycles:
```powershell
python scripts/test_networked_faults_and_failover.py
```
*Emits:* `distributed_fault_results.json`, `fencing_safety_results.json`, `acknowledged_write_recovery_results.json`, `failover_benchmark_results.json`, `failover_raw_samples.json`

### 4.3 Networked Fan-Out & Client Receipt Campaign
Validates real TCP socket broadcast across 1, 25, 50, and 100 clients, 90/10 noisy-neighbor eviction, and 25k event soak:
```powershell
python scripts/test_networked_fanout_receipt.py
```
*Emits:* `fanout_networked_stress_results.json`, `fanout_client_receipt_results.json`, `resource_utilization_results.json`, `fanout_soak_report.md`

### 4.4 End-to-End Pipeline & Forensic Historical Integrity
Ingests multi-feed ticks into durable WAL, validates quality quarantine, and tests CRC32 tamper detection, torn-tails, and SQLite integrity:
```powershell
python scripts/test_networked_durability.py
```
*Emits:* `end_to_end_integrity_results.json`, `historical_recovery_results.json`

### 4.5 Operational Drills Campaign
Executes 7 operational drills: operator failover, replica rolling restart, primary crash, network split fencing, corrupted segment quarantine, instant API key revocation, and zero-downtime backup & PITR restore:
```powershell
python scripts/test_operational_drills.py
```
*Emits:* `operational_drill_results.json`

### 4.6 Release Candidate Wheel Installation Verification
Builds and installs `mdrap-core` wheel into an isolated target and verifies imports outside the repository without `PYTHONPATH`:
```powershell
python scripts/verify_phase10_wheel.py
```
*Emits:* `package_install_validation.json`

---

## 5. Verification Checklist

1. [x] All test suites report 100% pass rate.
2. [x] 15/15 distributed fault scenarios verified with 0 split-brain incidents.
3. [x] 100 failover trials completed with p50 = 105.01 ms.
4. [x] Networked fan-out achieves > 26,000 frames/sec at client socket receipt boundary.
5. [x] 10/10 stalled noisy-neighbor clients cleanly evicted with 0 frame loss for fast clients.
6. [x] IngestLog bit-flips and torn-tails caught by CRC32 checksums and Merkle root verifier.
7. [x] Wheel installs cleanly into isolated environment with all package sentinels verified.
