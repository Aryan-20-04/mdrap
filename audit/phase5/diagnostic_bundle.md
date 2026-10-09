# MDRAP Phase 5 — Operational Diagnostic Bundle Specification

## 1. Executive Summary & Tool Purpose
When triaging incidents, performance degradation, or unexpected market feed anomalies, operations engineers require immediate, comprehensive visibility into host state, configuration, queue depths, memory consumption, and thread activity. However, exporting raw process state often risks exposing sensitive API tokens, secrets, or internal exchange credentials.

`scripts/diagnostic_bundle.py` is MDRAP's standard, zero-dependency diagnostic collection utility designed to generate complete, single-file forensic snapshots while guaranteeing **100% automated redaction of all sensitive secrets**.

---

## 2. Architecture and Data Capture Scope

The diagnostic utility captures five comprehensive operational domains:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       MDRAP DIAGNOSTIC ENGINE                               │
│                   (`scripts/diagnostic_bundle.py`)                          │
└───────┬─────────────────┬─────────────────┬─────────────────┬───────────────┘
        ▼                 ▼                 ▼                 ▼
 ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
 │ System & Host│  │ Architecture │  │ Configuration│  │ Storage & DB │
 │ - CPU & RAM  │  │ - Git commit │  │ - Environment│  │ - WAL size   │
 │ - Python env │  │ - Fastpath C │  │ - Sanitized  │  │ - IngestLog  │
 │ - Host info  │  │ - SHM status │  │   Config JSON│  │   Segment FDs│
 └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘
```

---

## 3. Automated Credential Redaction Invariant

The redaction engine recursively inspects all collected dictionaries, lists, and string payloads. Any dictionary key matching sensitive patterns (case-insensitive) is automatically replaced with `"[REDACTED]"`:

### Redaction Filter Rules:
- Keys matching: `*token*`, `*secret*`, `*key*`, `*password*`, `*auth*`, `*salt*`, `*credential*`.
- Any string value starting with `mdrap_live_` or `mdrap_test_` is replaced with `"[REDACTED_MDRAP_TOKEN]"`.
- Verified in automated test `test_diagnostic_bundle_redaction()` in `tests/test_phase5_pilot.py`.

---

## 4. Usage Syntax and Examples

### Standard CLI Invocation
```bash
python scripts/diagnostic_bundle.py --out /var/log/mdrap/diagnostics/bundle_$(date +%s).json
```

### JSON Output Schema Example:
```json
{
  "bundle_version": "1.0.0",
  "generated_at": "2026-10-09T17:05:00.123456Z",
  "system": {
    "platform": "Windows-11-10.0.26100-SP0",
    "python_version": "3.13.1",
    "cpu_count": 8,
    "memory_total_gb": 16.0,
    "memory_available_gb": 8.4
  },
  "git": {
    "commit": "462da61",
    "dirty": false
  },
  "runtime_capabilities": {
    "fastpath_c_available": true,
    "shm_posix_available": false,
    "shm_win32_available": true
  },
  "storage": {
    "canonical_db_size_bytes": 4812800,
    "wal_size_bytes": 1048576,
    "quarantine_count": 12
  },
  "configuration": {
    "ingress_port": 9002,
    "fsync_policy": "grouped_by_size",
    "api_token": "[REDACTED]"
  }
}
```

---

## 5. Integration with Incident Management
- **Automated PagerDuty Attachment**: When Alertmanager triggers a SEV-1 incident, an automated cron invokes `diagnostic_bundle.py` and attaches the JSON payload directly to the incident incident ticket within 30 seconds of onset.
- **Support Upload**: Customers can securely forward the resulting JSON bundle to MDRAP Support without NDA violation or risk of credential exposure.
