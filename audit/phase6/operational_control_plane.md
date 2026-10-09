# MDRAP Phase 6 — Operational Control Plane Specification

## 1. Executive Summary & Purpose
As deployments scale from single processes to partitioned multi-instance fleets, platform operators require deterministic administrative tooling to inspect health, drain specific shards, throttle abusive tenants, and initiate maintenance without restarting the entire platform.

This document specifies the operational control plane interfaces, security permissions, and audit protocols for MDRAP Phase 6.

---

## 2. Control Plane Interface & Command Suite

The administrative CLI (`cli.py`) orchestrates fleet-wide operations:

```bash
# 1. Inspect fleet health across all shards
python cli.py fleet status --format table

# 2. Gracefully drain a specific shard for localized host maintenance
python cli.py fleet drain --shard 0 --timeout 5.0

# 3. Forcibly evict a rogue or abusive consumer across all shards
python cli.py fleet evict-consumer --consumer-id DESK_ALPHA --reason "Backpressure SLA breach"

# 4. Preview partition rebalancing without applying changes (Dry-Run)
python cli.py fleet rebalance --mode range --shards 4 --dry-run
```

---

## 3. Administrative Security & Privilege Gating

All control plane commands enforce multi-tiered security checks:

1. **Role Verification**: Caller must supply an API token holding `Role.ADMIN`. Unprivileged tokens receive `403 Forbidden`.
2. **Audit Recording**: Every executed command writes an immutable event record to `/var/data/mdrap/audit/control.log`:
   ```json
   {
     "timestamp": "2026-10-09T17:35:00Z",
     "operator": "sre_lead_01",
     "action": "fleet_drain",
     "target_shard": 0,
     "status": "SUCCESS"
   }
   ```
3. **Destructive Action Confirmation**: Commands that pause ingestion or truncate historical WAL segments mandate explicit confirmation flags (`--yes` / `--force`).
