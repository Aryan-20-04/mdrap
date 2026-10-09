# Phase 4 Resilience & Chaos Engineering Test Plan

**Scope**: Fault injection, partition simulation, ungraceful crash recovery, and fencing integrity  
**Test Suite**: `tests/test_phase4_resilience.py`  
**Timestamp**: 2026-10-09  
**Status**: APPROVED & VERIFIED  

---

## 1. Resilience Objectives

MDRAP serves as an upstream gate for institutional execution and risk systems. It must satisfy strict recovery and failure bounds:
1. **Crash Survivability**: An ungraceful SIGKILL, power loss, or filesystem write interruption must never corrupt historical WAL data.
2. **Deterministic Corruption Isolation**: A single corrupted frame (CRC mismatch, partial write) must be isolated at the log tail and must never poison earlier committed transactions.
3. **Split-Brain Immunity**: In dual-node active-passive deployments, network partitions must not allow stale primaries to commit data or overwrite higher-epoch states.
4. **Transport Flapping Resilience**: Socket drops and sequence gaps must be audited without dropping subsequent good data.

---

## 2. Fault Scenarios & Verification Methods

| Fault Scenario ID | Description | Injected Fault | Expected Recovery Behavior |
| :--- | :--- | :--- | :--- |
| **FLT-WAL-001** | Mid-frame crash truncation | 15-byte tail truncation of active `.log` segment | Reader identifies incomplete record, safely recovers all prior valid frames, and halts cleanly. |
| **FLT-SBE-002** | Wire payload corruption | Undersized buffers (<64B) and IEEE NaN floating point price | Unpacker detects length violation; QualityEngine flags non-finite values as non-valid. |
| **FLT-HA-003** | Split-brain & stale writer | Primary node partition, standby promotes to Epoch 2; stale node attempts write | Fencing token enforcement (`assert_fencing_token`) raises `StaleEpochError`. |
| **FLT-ING-004** | Upstream feed sequence gap | Drop frames 102..104 from incoming stream | Ingress gap auditor flags `gaps_detected=1` and `missing_events_count=3` without halting stream. |
