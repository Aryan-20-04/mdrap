# MDRAP Phase 6 — Fleet Upgrade, Zero-Downtime Rolling Deployments, and Rollback Procedures

## 1. Executive Summary & Objective
Production market data systems cannot tolerate unplanned downtime during trading hours. This document formalizes the **Fleet Upgrade and Rollback Runbook** for MDRAP multi-shard clusters, enabling rolling software upgrades, schema migrations, and immediate zero-loss rollback procedures.

---

## 2. Upgrade Architecture: Shard-by-Shard Rolling Deployment

Because MDRAP employs orthogonal symbol partitioning (`src/partition.py`), individual shards can be upgraded sequentially without cross-shard lock dependencies or cluster-wide outages.

```
Step 1: Cluster at v1.0.0 (Shard 0 & Shard 1 Active)
[ Gateway ] ───┬───> [ Shard 0 (v1.0.0) ] ───> Consumers (A-L)
               └───> [ Shard 1 (v1.0.0) ] ───> Consumers (M-Z)

Step 2: Rolling Upgrade of Shard 0 to v1.1.0 (Shard 1 Uninterrupted)
[ Gateway ] ───┬───> [ DRAIN / RESTART ] ───> Buffer Ingest (A-L)
               └───> [ Shard 1 (v1.0.0) ] ───> Active Traffic (M-Z) (Zero Interruption)

Step 3: Verification & Health Gate
[ FleetCoordinator ] verifies Shard 0 health check: CRC32, latency, memory < budget

Step 4: Roll Forward to Shard 1
[ Gateway ] ───┬───> [ Shard 0 (v1.1.0) ] ───> Active Traffic (A-L)
               └───> [ Upgrade Shard 1  ] ───> Upgrade to v1.1.0
```

---

## 3. Rolling Upgrade Execution Procedure

### Pre-Upgrade Verification Gate
1. Validate staging artifacts: Run `python -m pytest tests/test_phase6_scaling.py` on the target release bundle.
2. Confirm backup status: Ensure all sealed WAL segments from previous trading sessions are synced to cold storage.
3. Verify cluster health: Confirm `FleetCoordinator.fleet_health()` reports all shards healthy with zero pending quarantine backlogs.

### Execution Steps
```bash
# 1. Drain and gracefully stop Shard 0
python cli.py fleet drain --shard-id 0 --timeout-sec 30
kill -TERM $(cat /var/data/mdrap/shard_0/shard.pid)

# 2. Deploy new binary / update virtualenv
git checkout tags/v1.1.0
pip install --no-deps -e .

# 3. Start Shard 0 on new version
python cli.py shard start --id 0 --config /etc/mdrap/shard_0.json &

# 4. Automated Health Verification Gate (Wait 60s)
python cli.py fleet check-health --shard-id 0 --max-p99-us 50 --max-drop-rate 0.0

# 5. Repeat steps 1-4 for Shard 1
python cli.py fleet drain --shard-id 1 --timeout-sec 30
kill -TERM $(cat /var/data/mdrap/shard_1/shard.pid)
python cli.py shard start --id 1 --config /etc/mdrap/shard_1.json &
```

---

## 4. Emergency Rollback Runbook

If any health gate fails (p99 latency $> 100\text{ \mu s}$, unhandled exceptions $> 0$, or memory growth $> 10\text{ MB/min}$):

### Rollback Steps
1. **Immediate Ingress Hold**: Ingest gateway pauses network ingress buffers for the failing shard (up to 60s memory buffer capacity).
2. **Process Reversion**:
   ```bash
   kill -9 $(cat /var/data/mdrap/shard_0/shard.pid)
   git checkout tags/v1.0.0
   python cli.py shard start --id 0 --config /etc/mdrap/shard_0.json &
   ```
3. **WAL Tail Recovery**: The prior binary version opens the existing WAL segment, scans to the last committed record, and resumes consumption.
4. **Data Continuity Guarantee**: Because WAL formats preserve forward-compatible header padding, no committed tick data is discarded during rollback.
