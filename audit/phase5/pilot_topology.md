# Phase 5 Pilot Topology Architecture

**Topology Profile**: Profile A — Single-Node High Throughput with Durable IngestLog WAL  
**Host Context**: Dedicated Service Instance (Local Isolated Staging)  
**Date**: 2026-10-09  

---

## 1. Process & Memory Map

```text
       ┌─────────────────────────────────────────────────────────┐
       │                   MDRAP Host Process                    │
       │                                                         │
       │  [Replay Feed Adapter]                                  │
       │           │                                             │
       │           ▼                                             │
       │  [Schema Normalization]                                 │
       │           │                                             │
       │           ▼                                             │
       │  [Quality Engine] ───────► [Quarantine Table]           │
       │           │ (VALID/SUSPICIOUS)                          │
       │           ▼                                             │
       │  ┌─────────────────┬─────────────────┐                  │
       │  ▼                 ▼                 ▼                  │
       │ [IngestLog WAL]   [SBE Encoder]   [Durable Meter]       │
       │ (/var/log/wal)    (64B wire)      (/var/db/meter.db)    │
       └─────────┬──────────────────┬────────────────────────────┘
                 │                  │
                 ▼                  ▼
     [Cold Replay / Verification] [IPC Socket / Ring Buffer]
                                            │
                                            ▼
                           [Independent Client Application]
                           (C++17 / Python SDK Consumer)
```

---

## 2. Storage & Filesystem Layout

- **Configuration Path**: `/etc/mdrap/mdrap.toml` (non-secret config file).
- **Environment Secrets**: `MDRAP_API_KEY_SALT`, `MDRAP_FEED_SECRET` (passed via protected environment variables).
- **WAL Directory**: `/var/lib/mdrap/wal/` (segmented `.log` files with exclusive `.lock`).
- **Accounting Database**: `/var/lib/mdrap/metering.db` (SQLite WAL mode).
- **Prometheus Metrics Port**: `http://127.0.0.1:8080/metrics`.
