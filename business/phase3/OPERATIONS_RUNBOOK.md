# MDRAP Phase 3 — Customer Operations Runbook

**Document Version**: 1.0.0  
**Target Audience**: Operations Engineers, SREs, Systems Administrators  
**Author**: Lead Infrastructure & Deployment Engineer (Agent 5)  
**Status**: APPROVED  

---

## 1. Daily Health & Observability Checklist

Operations personnel should run a routine health check at the beginning of each trading session:

```bash
python cli.py health --db data/mdrap_live.db
```

### Healthy Output Signature:
```json
{
  "status": "HEALTHY",
  "venues": {
    "COINBASE": {
      "connected": true,
      "staleness_ms": 112.4,
      "gaps": 0,
      "errors": 0
    },
    "KRAKEN": {
      "connected": true,
      "staleness_ms": 284.1,
      "gaps": 0,
      "errors": 0
    }
  },
  "ingest_rate_eps": 842.1,
  "uncommitted_tombstones": 0
}
```

### Action Thresholds:
- **`connected == false`**: Feed worker disconnected. Check upstream internet connectivity or exchange maintenance status.
- **`staleness_ms > 10,000`**: Feed is connected but no new frames have arrived for $> 10$ seconds (possible zombie socket). Restart worker.
- **`gaps > 5 / min`**: High network packet loss between host and exchange. Initiate network route trace.

---

## 2. Incident Investigation Procedure

When a trader or algorithmic model alerts on a suspected bad fill or market data spike:

### Step 1: List Recent Incidents
```bash
python cli.py incident list --db data/mdrap_live.db --limit 10
```
Identifies recent anomalies flagged by the QualityEngine (`SEQUENCE_GAP`, `CROSSED_BOOK`, `CLOCK_SKEW`, `FEED_STALE`).

### Step 2: Trigger Deterministic Replay
```bash
python cli.py replay --incident <INCIDENT_ID> --corridor 30 --db data/mdrap_live.db
```
- Reconstructs the exact BBO order book state for 30 seconds before and after the incident.
- Verifies whether the anomalous tick arrived before or after the customer's trade execution timestamp.
- Produces a deterministic state digest confirming reproduction.

### Step 3: Export Forensic Evidence Package
```bash
python cli.py export-evidence --incident <INCIDENT_ID> --output /tmp/evidence_<INCIDENT_ID>.json
```
- Exports a self-contained JSON manifest containing the raw wire payload, SHA-256 digest, lineage hash, and RFC 6962 Merkle proof.

### Step 4: Independent Verification
Send `/tmp/evidence_<INCIDENT_ID>.json` to risk, compliance, or external counterparty. Run:
```bash
python tools/verify_evidence.py /tmp/evidence_<INCIDENT_ID>.json
```
Confirms tamper-free authenticity with mathematical certainty.

---

## 3. Backup & Disaster Recovery Procedures

### Online SQLite WAL Hot Backup (Zero Downtime)
SQLite WAL mode allows safe, consistent hot backups while ingestion is actively occurring:
```bash
sqlite3 data/mdrap_live.db ".backup 'data/backups/mdrap_backup_$(date +%Y%m%d_%H%M%S).db'"
```

### Storage Retention Pruning (Automated Compaction)
To prevent disk exhaustion on high-frequency feeds, run the automated retention pruner weekly:
```bash
python cli.py prune --days 30 --db data/mdrap_live.db
```
Removes events older than 30 days while preserving all incident records and associated cryptographic proofs.

---

## 4. Rollback & Uninstallation

If MDRAP needs to be removed or temporarily rolled back:
```bash
# Stop systemd service
sudo systemctl stop mdrap.service
sudo systemctl disable mdrap.service

# Remove data if purging
rm -rf /var/lib/mdrap/data/*
```
*Impact*: Customer trading engines continue operating without disruption because MDRAP is completely out-of-band.
