# Phase 4 Backup & Restore Results

**Scope**: IngestLog snapshotting, SQLite vacuum/backup, and point-in-time restoration  
**Timestamp**: 2026-10-09  
**Status**: VERIFIED  

---

## 1. Storage Artifacts Backed Up

1. **IngestLog WAL Segments (`/var/lib/mdrap/wal/segment_*.log`)**:
   - Sealed segments (all segments prior to active segment) are read-only and immutable.
   - Backup strategy: Continuous streaming archive or rsync of completed `.log` segments.
2. **SQLite Durable Usage Metering (`/var/lib/mdrap/metering.db`)**:
   - SQLite WAL database.
   - Backup strategy: Online backup via `VACUUM INTO '/backup/metering_snap.db'` or SQLite Online Backup API.
3. **Configuration & Entitlement Stores (`mdrap.toml`, `keys.json`)**:
   - Ephemeral or VCS-managed version-controlled configurations.

---

## 2. Restore Procedure Verification

1. **IngestLog Restoration**:
   - Archived segment directory restored to fresh filesystem.
   - Instance initialized with `IngestLog(log_dir=restored_dir)`.
   - Result: Automatically detected highest offset, validated existing segment CRC headers, and opened a clean new segment.
2. **Metering DB Restoration**:
   - Restored SQLite database from vacuumed snapshot.
   - Executed `DurableUsageMeter(restored_db)`.
   - Result: Aggregates from prior run loaded without inconsistency or lock contention.
