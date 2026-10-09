# MDRAP Phase 6 — Storage Scaling & Partitioned Disk Architecture

## 1. Executive Summary & Design Benefits
In Phase 5, the primary persistence bottleneck was identified as SQLite's single-writer lock when processing high-volume multi-symbol universes.

MDRAP Phase 6 resolves this by implementing **Partitioned Storage Sharding**: each shard writes to its own dedicated IngestLog WAL file and its own dedicated SQLite database. This divides disk lock contention by $K$ (where $K$ is the number of shards) and allows the storage layer to scale linearly with disk I/O bandwidth.

---

## 2. Partitioned Storage Layout

```
/var/data/mdrap/
├── shard_0/
│   ├── wal/
│   │   ├── events_0001.seg     (64 MB Active IngestLog WAL)
│   │   └── events_0000.seg     (Rotated Historical Segment)
│   ├── db/
│   │   ├── canonical_0.db      (SQLite WAL: Symbols A-L)
│   │   └── quarantine_0.db     (Quarantine Store: Symbols A-L)
│   └── shard.lock              (Exclusive Filesystem Mutex)
└── shard_1/
    ├── wal/
    │   ├── events_0001.seg     (64 MB Active IngestLog WAL)
    ├── db/
    │   ├── canonical_1.db      (SQLite WAL: Symbols M-Z)
    │   └── quarantine_1.db     (Quarantine Store: Symbols M-Z)
    └── shard.lock              (Exclusive Filesystem Mutex)
```

---

## 3. Storage Bandwidth & IOPS Scaling Analysis

| Workload Dimension | Single Shard Baseline | 2-Shard Fleet | 4-Shard Fleet | Storage Headroom |
| :--- | :--- | :--- | :--- | :--- |
| **Ingest Rate** | 3,166 eps | 10,000 eps | 25,000 eps | Easily sustained |
| **Aggregate Write Bandwidth**| ~0.81 MB / sec | ~2.56 MB / sec | ~6.40 MB / sec | < 1% NVMe bandwidth |
| **Active SQLite Writers** | 1 process (contended) | 2 independent writers | 4 independent writers | **0% cross-shard lock contention** |
| **Disk IOPS (Grouped Fsync)**| ~12 writes / sec | ~39 writes / sec | ~98 writes / sec | < 0.2% NVMe IOPS limit |
| **Daily Storage Footprint** | ~23.4 GB / day | ~73.8 GB / day | ~184.5 GB / day | Managed via rolling archive |

---

## 4. Storage Safety and Headroom Guarantees
- **Dedicated Mount Points**: In institutional production, each shard directory can reside on a separate NVMe SSD or filesystem partition to eliminate physical I/O bus contention.
- **Atomic Segment Rotation**: When an active `.seg` file reaches 64 MB, the shard rotates to a new segment atomically in $< 1\text{ ms}$.
