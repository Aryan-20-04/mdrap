# MDRAP Cluster Failover Operational Runbook

## 1. Scope & Prerequisites

This runbook documents step-by-step operator commands for managing cluster failover, planned maintenance promotions, and split-brain recovery on MDRAP clusters.

### Prerequisites
- SSH or administrative CLI access to cluster nodes.
- Environment variable `MDRAP_API_KEY` set with `OPERATOR` or `ADMIN` role.
- Python 3.13+ environment with MDRAP installed (`python cli.py` accessible).

---

## 2. Cluster Health Inspection

Check live cluster state across primary and standby nodes:

```bash
# Inspect node status and peer telemetry
python cli.py failover status
```

Example Output:
```json
{
  "node_id": "mdrap-prod-02",
  "cluster_id": "mdrap-cluster-us-east",
  "state": "STANDBY",
  "epoch": 4,
  "fencing_token": 4,
  "heartbeats_sent": 1420,
  "heartbeats_received": 1419,
  "failover_count": 1,
  "last_primary_node_id": "mdrap-prod-01",
  "last_primary_age_s": 0.32,
  "state_duration_s": 8420.5
}
```

Verify that:
- Exactly one node reports `"state": "PRIMARY"`.
- Standby nodes report `"last_primary_age_s" < 1.0`.
- All nodes share the same `"epoch"`.

---

## 3. Routine Planned Maintenance Failover

Use this procedure to gracefully transition traffic from Primary (`Node-01`) to Standby (`Node-02`) with **zero tick loss**:

### Step 1: Pre-Failover Sequence Verification
On Standby (`Node-02`):
```bash
python cli.py failover status
```
Ensure `last_primary_age_s` is fresh (< 0.5s) and sequence gap is 0.

### Step 2: Demote Primary Node
On Primary (`Node-01`):
```bash
# Gracefully demote primary to standby (flushes SQLite and binary journals)
python cli.py failover demote --reason "Scheduled OS Kernel Patching"
```

### Step 3: Promote Standby Node
On Standby (`Node-02`):
```bash
# Promote standby to primary with incremented epoch
python cli.py failover promote
```

### Step 4: Verification
Confirm `Node-02` status:
```bash
python cli.py failover status
```
Verify `"state": "PRIMARY"` and epoch has incremented by +1.

---

## 4. Emergency Unplanned Failover

If the Primary node crashes or becomes uncontactable:

1. Automatic failover should engage within **2.0 seconds** (`heartbeat_timeout_s`).
2. If automatic failover is blocked or manual override is required:
   ```bash
   python cli.py failover promote --force
   ```
3. Audit log verification:
   ```bash
   python cli.py audit verify
   ```

---

## 5. Post-Failover Verification Checklist

- [ ] New primary node reports `"state": "PRIMARY"`.
- [ ] Ingress message rate (`mdrap_messages_ingested_total`) continues monotonically.
- [ ] Shared memory publishers and downstream strategy consumers receive live ticks.
- [ ] Audit log records `API_KEY_ROTATED` / `MERKLE_BATCH_ANCHOR` / `FAILOVER_PROMOTED`.
- [ ] No `SplitBrainDetected` or `StaleEpochError` warnings in logs.
