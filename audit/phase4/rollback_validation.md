# Phase 4 Rollback Validation Runbook

**Scope**: Safe rollback from v3.0.0 to previous stable release (v2.x)  
**Timestamp**: 2026-10-09  

---

## 1. Rollback Preconditions & Compatibility

- **Storage Backward Compatibility**:
  - `IngestLog` WAL segment headers maintain `LOG_VERSION = 1` which is compatible with earlier readers.
  - SQLite databases do not drop older tables during v3.0 startup.
- **Rollback Procedure**:
  1. Trigger failover to standby node if primary is unstable.
  2. Stop active service instance:
     ```bash
     systemctl stop mdrap
     ```
  3. Revert Python package to previous release:
     ```bash
     pip install mdrap==2.8.4
     ```
  4. Restore previous `mdrap.toml` or `config.yaml` configuration.
  5. Restart daemon and verify health check:
     ```bash
     systemctl start mdrap
     curl -s http://localhost:8080/health | jq .status
     ```
