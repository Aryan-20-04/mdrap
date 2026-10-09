# MDRAP Phase 6 — Configuration Governance & Drift Management

## 1. Executive Summary & Objective
Configuration inconsistencies between staging, canary, and production environments represent a primary root cause of production incidents. MDRAP Phase 6 enforces **Strict Schema-Validated Configuration Governance** and automated cryptographic drift detection.

---

## 2. Configuration Schema & Hierarchy

Configurations are structured into immutable declarative manifests validated against strong typing:

```json
{
  "fleet_id": "mdrap-prod-ny4",
  "num_shards": 2,
  "partition_mode": "range",
  "shards": [
    {
      "shard_id": 0,
      "name": "Shard_A_L",
      "sbe_port": 9002,
      "data_dir": "/var/data/mdrap/shard_0",
      "fsync_policy": "grouped_by_size",
      "max_queue_depth": 50000
    },
    {
      "shard_id": 1,
      "name": "Shard_M_Z",
      "sbe_port": 9003,
      "data_dir": "/var/data/mdrap/shard_1",
      "fsync_policy": "grouped_by_size",
      "max_queue_depth": 50000
    }
  ]
}
```

---

## 3. Automated Configuration Drift Detection

Every deployment host executes an automated drift detection check prior to launch:

```bash
# Verify active host configuration against the signed production manifest
python cli.py config check-drift --config /opt/mdrap/config/active.json --baseline /opt/mdrap/config/approved_baseline.json
```

### Drift Verification Rules:
1. **Cryptographic Checksum**: The SHA-256 hash of the sanitized configuration must match the release tag manifest.
2. **Schema Validation**: Any unknown configuration keys or out-of-bounds integer parameters raise a `ConfigValidationError` and immediately halt launch.
3. **Automated Pytest Coverage**: Validated via `test_configuration_drift_detection` in `tests/test_phase5_pilot.py`.
