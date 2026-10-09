# MDRAP Phase 11 — Complete Audit Reproduction Commands Runbook

## 1. Executive Summary
In compliance with Phase 11 Mandatory Rules §1.11, §1.15, and Institutional Design Principle §9 ("Deterministic experiments"), this document provides the exact, reproducible command invocations, environment variables, execution sequences, and expected exit codes for all verification campaigns in MDRAP Phase 11.

---

## 2. Environment Setup & Prerequisites

```powershell
# Set repository root and staging security environment variables
cd c:\Users\KIIT0001\Desktop\Projects\mdrap
$env:PYTHONPATH = ".;src"
$env:MDRAP_API_KEY_SALT = "staging_cluster_salt_phase11_secret"
$env:MDRAP_DAEMON_TOKEN = "staging_admin_token_phase11"
$env:MDRAP_DISABLE_FASTPATH = "0"
```

---

## 3. Step-by-Step Reproduction Commands

### Step 1: Baseline Test Suite Regression (1,240 Core Tests)
```powershell
python -m pytest tests/ -q
# Expected result: 1240 passed, 60 deselected in ~160 seconds (exit code 0)
```

### Step 2: TCP Network Connectivity & Inter-Node Latency Probe
```powershell
python scripts/test_phase11_network_probe.py
# Expected result: Emits audit/phase11/network_connectivity_results.json
```

### Step 3: 10-Scenario Consensus & Persistence Fencing Audit
```powershell
python scripts/test_phase11_fencing_audit.py
# Expected result: 10 passed, 0 failed. Mean interception latency ~1.5 us.
# Emits audit/phase11/consensus_and_fencing_results.json
```

### Step 4: Distributed Fault Campaign & 100-Trial Failover Benchmark
```powershell
python scripts/test_phase11_distributed_faults_and_failover.py
# Expected result: 15 scenarios passed, failover p50 ~105 ms.
# Emits:
# - audit/phase11/distributed_fault_results.json
# - audit/phase11/acknowledged_write_recovery_results.json
# - audit/phase11/failover_benchmark_results.json
# - audit/phase11/failover_raw_samples.json
```

### Step 5: Networked Fan-Out & Client Receipt Benchmark
```powershell
python scripts/test_phase11_fanout.py
# Expected result: 1, 25, 50, 100 client tiers, 10/10 noisy-neighbor evictions, 25k soak.
# Emits:
# - audit/phase11/fanout_networked_results.json
# - audit/phase11/fanout_client_receipt_results.json
# - audit/phase11/resource_utilization_results.json
```

### Step 6: End-to-End Pipeline & Forensic Historical Integrity
```powershell
python scripts/test_phase11_durability.py
# Expected result: 2,000 events ingested, Merkle root computed, CRC32 bit-flip caught, torn tail caught.
# Emits:
# - audit/phase11/durability_and_replay_results.json
# - audit/phase11/historical_integrity_results.json
```

### Step 7: 7 Institutional Operational Drills
```powershell
python scripts/test_phase11_operational_drills.py
# Expected result: 7 drills passed (operator failover, rolling restart, key revocation, PITR restore).
# Emits audit/phase11/operational_drill_results.json
```

### Step 8: Isolated Wheel Package Installation Verification
```powershell
python scripts/verify_phase11_wheel.py
# Expected result: Wheel installed in temporary directory, python -S verified.
# Emits audit/phase11/package_install_validation.json
```

### Step 9: Generate Evidence Manifest with SHA-256 Hashes
```powershell
python scripts/generate_phase11_evidence_manifest.py
# Expected result: Generates audit/phase11/evidence_manifest.json cataloging all deliverables.
```

---

## 4. Verification & Integrity Checklist
Every generated JSON artifact can be verified in a single command:
```powershell
python -c "import os, json; [json.load(open(os.path.join('audit/phase11', f))) for f in os.listdir('audit/phase11') if f.endswith('.json')]; print('All Phase 11 JSON files parse cleanly!')"
```
