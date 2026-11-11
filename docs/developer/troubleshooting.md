# Operational Troubleshooting Guide

This guide covers common operational challenges, error messages, and remediation procedures for MDRAP.

---

## 1. Common Diagnostic Scenarios

### Error: `ValueError: MDRAP_API_KEY_SALT must be set to a non-empty value outside demo mode`
- **Cause**: Security hardening requires a unique cryptographic salt when starting the API service outside testing/demo mode.
- **Remediation**: Set the salt in your environment or configuration file:
  ```bash
  export MDRAP_API_KEY_SALT=$(openssl rand -hex 32)
  ```

### Warning: `SQLite Busy / Database is Locked`
- **Cause**: Multiple processes attempting concurrent write operations without WAL mode enabled, or WAL checkpointing stalling on slow disk.
- **Remediation**:
  1. Confirm WAL mode is active:
     ```bash
     sqlite3 data/mdrap.db "PRAGMA journal_mode;"
     ```
     Should return `wal`.
  2. Increase busy timeout in `config.toml`:
     ```toml
     [storage]
     busy_timeout_ms = 10000
     ```
  3. Ensure database is hosted on local high-speed SSD/NVMe, not NFS or SMB network shares.

### Warning: `ClockSkew Anomaly Detected`
- **Cause**: Exchange timestamp differs from host system clock by more than configured threshold (default: $50\text{ms}$).
- **Remediation**:
  1. Verify host system clock synchronization via NTP or PTP:
     ```bash
     timedatectl status
     ```
  2. If running on public internet without PTP, tune `max_clock_drift_ms` in `config.toml` (e.g. increase to $100.0\text{ms}$).

### Warning: `SequenceGap Anomaly Detected`
- **Cause**: Exchange dropped packets due to local network congestion or upstream exchange load shedding during high-volatility spikes.
- **Remediation**:
  1. Check network interface drop counters (`netstat -s` or `ifconfig`).
  2. Review the generated incident in `cli.py incident list`.
  3. Run deterministic replay across the incident corridor to evaluate exact market impact.

---

## 2. Health & Self-Healing Utilities

Run the built-in diagnostic scan:
```bash
python cli.py doctor
```

Check database integrity:
```bash
sqlite3 data/mdrap.db "PRAGMA integrity_check;"
```

Recover an uncommitted WAL journal:
```bash
sqlite3 data/mdrap.db "PRAGMA wal_checkpoint(TRUNCATE);"
```
