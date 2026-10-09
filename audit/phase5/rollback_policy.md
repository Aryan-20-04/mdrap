# MDRAP Phase 5 — Rollback and Abort Policy

## 1. Scope and Objective
This policy defines the precise, deterministic procedures for reversing a deployment or configuration change to the Market Data Reliability & Acceleration Platform (MDRAP). The primary objective during any rollback is preserving data integrity, preventing sequence duplication, and minimizing consumer downtime (Target **RTO < 3 minutes**, **RPO = 0** for fsynced events).

---

## 2. Rollback Triggers

A rollback is mandatory upon occurrence of any of the following triggers:

### 2.1 Automated Triggers
- Service process exit or crash within 15 minutes of startup.
- Prometheus health probe failure (`http://127.0.0.1:8000/health/ready` returns non-200 for 3 consecutive polls).
- Unhandled schema validation error rate > 0.1% across incoming messages.
- Consumer socket disconnection rate > 20% within a 60-second window.

### 2.2 Operational / Human Triggers
- Trading Desk Commander issues a formal stop-work directive.
- SRE on-call observes persistent un-reconcilable cross-feed price disagreement > 100 bps.
- Security finding indicating an active vulnerability or credential exposure.

---

## 3. Step-by-Step Rollback Execution Procedure

```
[ Step 1: Drain Ingress ] ──> [ Step 2: Stop Service ] ──> [ Step 3: Verify Storage ]
                                                                     │
[ Step 6: Post-Rollback PIR ] <── [ Step 5: Verify Consumers ] <── [ Step 4: Revert Release ]
```

### Step 1: Drain & Fence Ingress
Immediately halt incoming network packets from external feeds to prevent additional unvalidated events from entering the degraded node:
```bash
# Graceful stop command to close ingest sockets and drain memory buffers
python cli.py stop --drain-timeout 5.0
```

### Step 2: Process Termination and Port Verification
Ensure all MDRAP worker threads, SHM drainers, and API servers are completely halted:
```bash
# Inspect running processes and terminate if still resident
python -c "import psutil; [p.kill() for p in psutil.process_iter() if 'mdrap' in p.name().lower()]"
```

### Step 3: Journal and Storage Integrity Verification
Verify that the `IngestLog` WAL and SQLite databases are not in a locked or corrupted state:
```bash
# Run integrity check on primary storage
python -c "
import sqlite3
con = sqlite3.connect('/var/data/mdrap/db/canonical.db')
cursor = con.execute('PRAGMA integrity_check;')
assert cursor.fetchone()[0] == 'ok', 'Database corruption detected!'
con.close()
print('Storage integrity: OK')
"
```

### Step 4: Revert Binary and Configuration Symlinks
Re-point the production deployment symlink to the previous verified release artifact:
```bash
# Atomically switch current pointer back to previous validated release
ln -sfn /opt/mdrap/releases/v1.0.0-rc4 /opt/mdrap/current

# Restore previous configuration file
cp /opt/mdrap/config/backups/pilot_profile_a.json.prev /opt/mdrap/config/pilot_profile_a.json
```

### Step 5: Start Previous Version and Verify Consumer Flow
Restart the daemon using the previous version and verify health checks:
```bash
/opt/mdrap/current/bin/mdrap start --config /opt/mdrap/config/pilot_profile_a.json

# Poll readiness probe until HTTP 200
curl -f -s http://127.0.0.1:8000/health/ready || exit 1
```

### Step 6: Verify Sequence Monotonicity on Consumers
1. Inspect consumer logs to confirm SBE socket reconnection.
2. Confirm downstream consumers resume receiving ticks with continuous sequence continuity.
3. If sequence gap occurred during the restart window, initiate automated backfill from IngestLog:
   ```bash
   python cli.py replay --from-seq <last_consumer_seq> --to-seq <current_head_seq>
   ```

---

## 4. Data Compatibility and Schema Invariants

To guarantee that any rollback is seamless and does not corrupt persistent storage:

1. **Append-Only Schema Law**: Database tables (`raw_events`, `canonical_events`, `quarantine_events`, `merkle_audit`) are strictly append-only. No migrations may drop columns or alter primary key layouts.
2. **Backward-Compatible SBE Messages**: The SBE template versioning mechanism ensures newer fields are placed in trailing optional blocks. Older engine versions ignore trailing bytes without failing frame deserialization.
3. **Quarantine Isolation**: Any event written by a newer version that cannot be parsed by the rolled-back engine is routed to the quarantine queue rather than halting the ingest pipeline.

---

## 5. Post-Rollback Actions & Triage

1. **Preserve Forensic Evidence**: Immediately execute the diagnostic capture utility to preserve all logs, metrics, and memory dumps from the failed deployment:
   ```bash
   python scripts/diagnostic_bundle.py --out /var/log/mdrap/incidents/failed_deploy_$(date +%s).json
   ```
2. **Release Quarantining**: Tag the failed release candidate as `BLOCKED_DEFECTIVE` in git and artifact repositories.
3. **PIR Scheduling**: Schedule mandatory Post-Incident Review within 24 hours.
