# Phase 5 Deployment Automation & Verification Guide

**Module**: `scripts/deploy_pilot.py`  
**Test Suite**: `tests/test_phase5_pilot.py::test_deployment_preflight_automation`  
**Timestamp**: 2026-10-09  
**Status**: VERIFIED PASS  

---

## 1. Automated Deployment Pipeline

The production deployment process is automated end-to-end via `scripts/deploy_pilot.py`:
1. **Prerequisite Inspection**: Validates Python runtime (>= 3.11) and 64-bit architecture.
2. **Directory Tree Provisioning**: Initializes `wal/`, `data/`, and `logs/` directories.
3. **Storage Perms Enforcement**: Verifies advisory lock creation (`.lock`) and filesystem write accessibility.
4. **End-to-End Smoke Verification**: Ingests, normalizes, evaluates, and commits a test trade to IngestLog WAL, verifying cold replay before approving service startup.

---

## 2. Execution Command & Output

```bash
python scripts/deploy_pilot.py
```

Output:
```text
=== MDRAP Automated Deployment Preflight ===
[*] Prerequisite check 'python_version_ge_3_11': OK
[*] Prerequisite check '64_bit_architecture': OK
[+] Initialized deployment paths under: temp_pilot_deploy
[+] Deployment preflight and smoke verification succeeded.
```

Exit code: `0` (Success). Any prerequisite failure immediately halts with exit code `1` and actionable stderr output.
