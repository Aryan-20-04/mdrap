# MDRAP Phase 5 — Data Lifecycle, Tiering, and Archival Operations

## 1. Executive Summary & Lifecycle Philosophy
Market data infrastructure generates massive data volumes that cannot remain indefinitely on high-performance primary NVMe storage without causing I/O degradation and storage exhaustion. MDRAP implements a three-tier data lifecycle architecture: **Tier 1 (Hot Active)**, **Tier 2 (Warm Intraday/Weekly)**, and **Tier 3 (Cold Immutable Archive)**. Data transitions occur deterministically, with end-to-end cryptographic integrity verification preserving regulatory audit chains across all tiers.

---

## 2. Storage Tier Architecture & Invariants

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ TIER 1: HOT ACTIVE (0 - 24 Hours)                                           │
│  - Storage Medium: Local PCIe Gen4 NVMe SSD                                 │
│  - Formats: IngestLog WAL (.seg), SQLite WAL (canonical.db, quarantine.db)   │
│  - Purpose: Live tick processing, real-time SBE distribution, zero-loss WAL │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ (EOD Transition: Rotate & Checkpoint)
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ TIER 2: WARM STORAGE (24 Hours - 7 Days)                                    │
│  - Storage Medium: Local SSD / High-Speed Storage Array                      │
│  - Formats: Closed IngestLog Segments (.seg), Checkpointed SQLite DBs       │
│  - Purpose: Intraday replay, consumer gap recovery, weekly TCA audit       │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ (Automated Zstandard Compaction)
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ TIER 3: COLD IMMUTABLE ARCHIVE (8 Days - 7 Years)                           │
│  - Storage Medium: Enterprise Object Storage (S3 / Ceph / WORM Storage)     │
│  - Formats: Columnar Zstandard Compressed Parquet / Tar.zst                 │
│  - Purpose: Regulatory audit (FINRA 4511 / SEC 17a-4), long-term quant backtest│
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Tier Transition Protocols

### 3.1 Transition 1: Hot to Warm (End of Trading Session)
- **Schedule**: Every trading day at 17:00:00 EST (22:00:00 UTC).
- **Automation**: Executed via systemd timer or scheduled task:
  ```bash
  python cli.py storage checkpoint --db /var/data/mdrap/db/canonical.db
  python cli.py storage rotate-segments --log-dir /var/data/mdrap/wal/
  ```
- **Integrity Gate**: SQLite `PRAGMA wal_checkpoint(TRUNCATE)` executed. CRC32 checksums of all completed `.seg` files recorded in `merkle_audit` table.

### 3.2 Transition 2: Warm to Cold (Weekly Compaction & Offload)
- **Schedule**: Weekly on Saturday 02:00:00 UTC.
- **Automation**: Executed via `src/archive.py`:
  ```bash
  python -m src.archive compress --source /var/data/mdrap/wal/ --dest /var/archive/mdrap/ --compression zstd --level 19
  ```
- **Integrity Gate**:
  - Uncompressed records verified against original Merkle tree root.
  - SHA-256 hash of final `.tar.zst` archive written to immutable ledger.
  - Successfully verified source `.seg` files removed from primary NVMe partition.

---

## 4. Replay and Retrieval Service Level Agreements (SLAs)

| Tier | Retention Period | Retrieval SLA | Access Interface | Compression Ratio |
| :--- | :--- | :--- | :--- | :--- |
| **Tier 1 (Hot)** | 0 – 24 hours | **< 10 milliseconds** | In-memory / IngestLog WAL | 1:1 (Raw binary) |
| **Tier 2 (Warm)** | 1 – 7 days | **< 2 seconds** | SQLite Index / CLI replay | ~1.8:1 (B-Tree) |
| **Tier 3 (Cold)** | 8 days – 7 years | **< 15 minutes** | Parquet Columnar / S3 fetch| ~6.5:1 (Zstandard) |

---

## 5. Deletion and Destruction Governance

- **Regulatory Lock**: In accordance with SEC Rule 17a-4, market data audit trails, raw event hashes, and accounting logs are locked in WORM (Write Once, Read Many) compliance mode for 7 years.
- **Cryptographic Erasure**: Following expiration of the mandatory 7-year retention period, storage volumes or object blocks undergo certified cryptographic erasure (DoD 5220.22-M or NIST SP 800-88).
