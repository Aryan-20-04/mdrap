# MDRAP Phase 6 — Fleet Rolling Upgrade & Zero-Downtime Migration Strategy

## 1. Executive Summary & Design Overview
In a partitioned deployment, taking the entire platform offline for software updates disrupts continuous trading. Because shards operate as decoupled, autonomous units, MDRAP Phase 6 enables **Rolling Shard-by-Shard Upgrades**, allowing software updates to be applied sequentially with zero total platform downtime.

---

## 2. Rolling Upgrade Workflow

```
[ Pre-Flight Manifest Lint ] ──> [ Drain Shard 0 ] ──> [ Upgrade Shard 0 Binary ]
                                                              │
                                                              ▼
[ Fleet Upgrade Complete ] <── [ Upgrade Shard 1 ] <── [ Verify Shard 0 Health (200 OK) ]
```

### Step 1: Pre-Upgrade Verification
- Ensure Git commit and binary artifact hashes match the signed release manifest.
- Validate configuration schema via `scripts/deploy_pilot.py --check-only`.

### Step 2: Shard 0 Drain & Binary Switch
1. Disconnect incoming feed ingestion to Shard 0; drain in-flight queue items:
   ```bash
   python cli.py fleet drain --shard 0 --timeout 5.0
   ```
2. Atomically switch release symlink for Shard 0:
   ```bash
   ln -sfn /opt/mdrap/releases/v1.0.0-phase6 /opt/mdrap/instances/shard_0/current
   ```
3. Restart Shard 0 daemon and verify readiness probe (`/health/ready`).

### Step 3: Shard 1 Drain & Binary Switch
- Once Shard 0 is verified healthy and processing symbols `A-L`, repeat the exact sequence for Shard 1 (`M-Z`).

---

## 3. Availability and Impact Assessment
- **Cross-Shard Continuity**: While Shard 0 is upgrading, Shard 1 continues serving symbols `M-Z` with zero interruption.
- **Consumer Reconnection**: Consumers for symbols `A-L` experience a localized ~2-second reconnect window, automatically backfilling missing ticks from IngestLog WAL upon reconnect.
- **Total Fleet Availability**: At no point is the entire platform offline simultaneously.
