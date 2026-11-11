# Pilot Exit & Clean Decommissioning Procedure

This document defines the standard protocol for concluding an MDRAP Design Partner Pilot evaluation, whether transitioning to an Enterprise License or cleanly decommissioning the sidecar.

---

## 1. Clean Teardown Protocol

Because MDRAP operates as a self-hosted sidecar, decommissioning leaves zero residual background processes or remote dependencies:

### Containerized Environment (Docker Compose)
```bash
cd deploy
docker compose down -v
docker rmi mdrap:6.0.0
```

### Bare-Metal / Virtual Machine (systemd)
```bash
sudo systemctl stop mdrap.service
sudo systemctl disable mdrap.service
sudo rm /etc/systemd/system/mdrap.service
sudo systemctl daemon-reload
sudo pip uninstall -y mdrap_core
```

---

## 2. Forensic Data Retention & Archival

All market-data logs, event provenance records, and incident corridors reside locally in the SQLite database directory (e.g. `/data` or `/var/lib/mdrap`).

### Archiving Evidence for Compliance
To archive all captured incidents and Merkle proofs prior to host decommissioning:
```bash
# 1. Checkpoint WAL into main database
sqlite3 /data/mdrap.db "PRAGMA wal_checkpoint(TRUNCATE);"

# 2. Create compressed forensic archive
tar -czvf mdrap_pilot_evidence_$(date +%F).tar.gz /data/mdrap.db /data/incidents/
```

### Sanitizing Data Directory
If host hardware is being re-provisioned:
```bash
sudo rm -rf /data /etc/mdrap /var/lib/mdrap
sudo userdel mdrap
```

---

## 3. Mutual Evaluation Sign-Off
Upon completion of the 30-day pilot:
1. Conduct final retrospective meeting using the Weekly Review Scorecard.
2. Provider delivers the final Design Partner Assurance Summary.
3. Design Partner signs mutual exit confirmation indicating either:
   - `[ ] Transition to Commercial Enterprise License`
   - `[ ] Clean Decommissioning with Retained Forensic Data`
