# MDRAP Phase 9 — SRE Operational Runbooks

## Runbook Overview & Scope
This document provides standard operating procedures (SOPs) and emergency runbooks for SREs, platform engineers, and market-data support teams managing MDRAP clusters in staging, UAT, and production environments.

---

## Runbook 1: Routine Cluster Startup & Health Check

### Objective
Start a high-availability MDRAP pair (Primary and Standby) with clean consensus state and verified storage integrity.

### Prerequisites
- Python 3.11+ environment with native fastpath extensions compiled.
- Configuration file `config.yaml` loaded with authorized feed endpoints.
- Secret environment variable set: `export MDRAP_API_KEY_SALT="<institution-salt>"`

### Step-by-Step Execution
1. **Initialize & Verify Storage Directories**:
   ```bash
   mkdir -p data/wal data/db data/quarantine
   chmod 700 data/wal data/db
   ```
2. **Start Primary Node (Port 8000)**:
   ```bash
   python scripts/deploy_uat_cluster.py --action start --node primary --port 8000
   ```
3. **Verify Primary Readiness**:
   ```bash
   curl -s http://127.0.0.1:8000/ready | grep "OK"
   curl -s http://127.0.0.1:8000/metrics | grep "mdrap_failover_is_primary 1"
   ```
4. **Start Standby Node (Port 8001)**:
   ```bash
   python scripts/deploy_uat_cluster.py --action start --node standby --port 8001
   ```
5. **Verify Standby State**:
   ```bash
   curl -s http://127.0.0.1:8001/metrics | grep "mdrap_failover_is_primary 0"
   ```

---

## Runbook 2: Controlled Rolling Restart / Binary Upgrade

### Objective
Upgrade MDRAP core binary or restart nodes without dropping live market ticks or violating stream sequence continuity.

### Step-by-Step Execution
1. **Step Down Standby Node**:
   ```bash
   kill -TERM <STANDBY_PID>
   ```
2. **Upgrade Standby Node Software / Packages**:
   ```bash
   pip install --no-deps dist/mdrap-3.0.0-py3-none-any.whl
   ```
3. **Restart Standby Node**:
   ```bash
   python -m mdrap.service --config config_standby.yaml &
   ```
   *Verify standby syncs to the current epoch and reports healthy.*
4. **Gracefully Demote Primary to Trigger Seamless Failover**:
   ```bash
   curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN" http://127.0.0.1:8000/api/v1/cluster/stepdown
   ```
   *Standby immediately acquires consensus lease, increments epoch to \(E+1\), and promotes to Primary.*
5. **Upgrade & Restart Former Primary as New Standby**:
   ```bash
   kill -TERM <OLD_PRIMARY_PID>
   pip install --no-deps dist/mdrap-3.0.0-py3-none-any.whl
   python -m mdrap.service --config config_primary.yaml --standby &
   ```

---

## Runbook 3: Split-Brain Remediation & Fencing Breach

### Symptom
- Warning alert: `FencingError: Writer epoch X < cluster epoch Y; write rejected`.
- Two processes attempting concurrent writes or claiming primary status after a network partition.

### Immediate Action
1. **Identify Authoritative Lease Holder**:
   Query cluster coordination service or consensus state file:
   ```bash
   cat data/consensus_lease.json
   ```
   Check `current_epoch` and `holder_id`.
2. **Isolate & Hard-Kill Zombie Node**:
   Identify the process with the stale epoch and terminate immediately:
   ```bash
   kill -9 <STALE_NODE_PID>
   ```
3. **Verify WAL Fencing Integrity**:
   Verify that `FencedWALWriter` prevented any corruption from the zombie node:
   ```bash
   python -m mdrap.historical_verifier --store data/db/market_data.db
   ```
   *Ensure `PRAGMA integrity_check` returns `ok` and zero sequence regressions occurred.*

---

## Runbook 4: Quarantine Triage & Poison-Pill Investigation

### Symptom
- Metric spike: `mdrap_events_quarantined_total` increasing rapidly.
- Alert: `Quarantine threshold exceeded on FEED_X`.

### Investigation Steps
1. **Sample Quarantined Records via CLI**:
   ```bash
   python -m mdrap.cli quarantine list --limit 20
   ```
2. **Inspect Violation Reasons**:
   Categorize based on reason code:
   - `SCHEMA_VIOLATION`: Upstream vendor modified feed JSON/SBE schema. Contact exchange technical support.
   - `PRICE_SANITY`: Flash crash, stale price, or massive unannounced split.
   - `CROSSED_QUOTE`: Venue order book inverted ($Bid > Ask$).
   - `SEQUENCE_GAP`: Upstream packet loss on UDP/multicast line. Trigger retransmission replay.
3. **Export Malformed Payloads for Exchange Dispute**:
   ```bash
   python -m mdrap.cli quarantine export --output /tmp/feed_x_quarantine_audit.json
   ```

---

## Runbook 5: Slow Consumer Eviction & Backpressure Triage

### Symptom
- Warning: `[AsyncFanout] Consumer ALGO_04 buffer saturated (depth=950/1000)`.
- Error: `[AsyncFanout] Evicted consumer ALGO_04 after 50 consecutive drops`.

### Resolution Steps
1. **Identify Saturation Source**:
   Check consumer health and network socket egress metrics:
   ```bash
   curl -s http://127.0.0.1:8000/api/v1/fanout/clients
   ```
2. **Notify Downstream Consumer Team**:
   Inform consumer `ALGO_04` that their process stalled, reached the eviction threshold, and was dropped to protect core platform stability.
3. **Reconnect with Expanded Queue Ceiling**:
   If consumer is legitimately high-latency by design (e.g., deep learning feature pipeline), grant custom queue capacity upon reconnect:
   ```bash
   curl -X POST -d '{"client_id": "ALGO_04", "max_queue_size": 20000}' http://127.0.0.1:8000/api/v1/fanout/register
   ```

---

## Runbook 6: Disaster Recovery from IngestLog Write-Ahead Replay

### Symptom
- SQLite database corrupted or lost due to disk volume failure.

### Recovery Execution
1. **Ensure Cluster Ingestion is Halted**:
   ```bash
   python scripts/deploy_uat_cluster.py --action stop
   ```
2. **Verify IngestLog WAL Segments**:
   ```bash
   python -m mdrap.historical_verifier --wal data/wal/
   ```
   *Verify 100% of CRC32 checksums pass and Merkle root is valid.*
3. **Replay IngestLog into Fresh SQLite Store**:
   ```bash
   python -m mdrap.replay --wal data/wal/ --target-db data/db/recovered_market_data.db
   ```
4. **Validate Reconstructed Tables**:
   ```bash
   sqlite3 data/db/recovered_market_data.db "SELECT COUNT(*) FROM canonical_events;"
   ```
5. **Restart Primary Node with Recovered Database**:
   ```bash
   python scripts/deploy_uat_cluster.py --action start --node primary --port 8000
   ```
