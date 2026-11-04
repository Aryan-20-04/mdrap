# MDRAP v5 Projection Model: Asynchronous SQLite & Sharded Storage

## 1. Executive Summary

This document specifies the asynchronous projection and relational persistence architecture for MDRAP v5.0.

In MDRAP v4.0.1, SQLite write operations were placed synchronously on the ingestion path. Even with SQLite WAL mode enabled, single-writer B-Tree locks and filesystem syncs throttled throughput to **2,767.8 EPS** and introduced tail latencies exceeding **14 ms** (`audit/v5/raw_evidence/benchmark_full_pipeline.log`).

MDRAP v5 completely removes SQLite from the hot ingestion path:
1. **Decoupled Projection Worker**: Ingestion commits events to the sequential binary `IngestLog` and enqueues pointers to an asynchronous SPSC lock-free ring buffer.
2. **Micro-Batched `executemany` Inserts**: A dedicated background projection worker pinned to an Efficiency Core (E-Core) drains batches of 1,000 events and issues bulk SQLite inserts.
3. **Optimized SQLite Ingestion PRAGMAs**: Configured for high-throughput append-only ingestion with 64 MB in-memory page caching and 256 MB memory-mapped I/O.
4. **Resilient Catchup via WAL Offset**: If the projection queue saturates under massive bursts, the hotpath drops projection notifications without stalling. The projection worker automatically catches up by scanning the persistent `IngestLog`.

---

## 2. Decoupled Asynchronous Projection Architecture

```mermaid
flowchart LR
    Hotpath["Hotpath Ingress Engine\n(P-Core 0)"] -->|1. Append Frame| WAL[("Sequential Binary\nIngestLog (Disk)")]
    Hotpath -->|2. Enqueue Pointer| SPSC["Bounded SPSC Ring\n(262,144 Slots)"]
    
    subgraph "Asynchronous Projection Plane (E-Core 4)"
        SPSC -->|3. Dequeue Batch| Drain["Micro-Batch Drainer\n(1,000 Slots or 10ms)"]
        Drain -->|4. executemany()| SQLite[("SQLite Canonical Database\n(PRAGMA wal, synchronous=NORMAL)")]
        SQLite -->|5. Update| Checkpoint["Projection Checkpoint Table\n(last_projected_seq)"]
    end

    subgraph "Burst / Saturation Fallback"
        Drain -.->|If SPSC Overflows| ScanWAL["Scan IngestLog from\nlast_projected_seq"]
        ScanWAL -.-> Drain
    end
```

---

## 3. High-Throughput SQLite Engine Configuration

To eliminate disk I/O bottlenecks in the background projection thread, SQLite is initialized with optimal high-throughput PRAGMA settings:

```sql
-- High-throughput WAL mode configuration
PRAGMA journal_mode = WAL;               -- Concurrent reads during active writes
PRAGMA synchronous = NORMAL;            -- Fsync only during checkpoint; safe in WAL mode
PRAGMA cache_size = -65536;             -- 64 MB in-memory page cache
PRAGMA temp_store = MEMORY;             -- Temporary tables/indices stored in RAM
PRAGMA mmap_size = 268435456;           -- 256 MB memory-mapped I/O
PRAGMA page_size = 4096;                -- 4 KB pages matching NVMe block size
PRAGMA wal_autocheckpoint = 10000;      -- Checkpoint every 10,000 pages (~40 MB)
```

### Safety and Durability with `synchronous = NORMAL`:
- In SQLite WAL mode, `PRAGMA synchronous = NORMAL` does not execute `fsync` after every single transaction commit.
- Transactions are committed to the `.db-wal` file in memory/OS page cache and flushed during checkpoints.
- In the event of a process crash or OS reboot, SQLite's WAL integrity is preserved.
- In the event of sudden power loss, SQLite guarantees internal database file consistency without corruption. Any un-checkpointed transactions in the database are recovered cleanly from MDRAP's authoritative binary `IngestLog`.

---

## 4. Micro-Batched Ingestion Implementation

The projection worker aggregates events into contiguous parameter arrays and executes a single multi-row `executemany` statement:

```c
// Native C / Python Projection Worker Loop
void run_projection_worker(ProjectionContext* ctx) {
    NativeSlot batch[BATCH_SIZE]; // 1,000 slots
    uint32_t count = 0;
    uint64_t last_flush_ts = get_monotonic_ns();

    sqlite3_stmt* stmt = ctx->insert_stmt;

    while (ctx->running) {
        NativeSlot slot;
        while (count < BATCH_SIZE && spsc_dequeue(ctx->queue, &slot)) {
            batch[count++] = slot;
        }

        uint64_t now = get_monotonic_ns();
        bool timeout = (now - last_flush_ts) >= FLUSH_TIMEOUT_NS; // 10 ms

        if (count >= BATCH_SIZE || (count > 0 && timeout)) {
            // Begin SQLite Transaction
            sqlite3_exec(ctx->db, "BEGIN IMMEDIATE TRANSACTION;", NULL, NULL, NULL);

            for (uint32_t i = 0; i < count; ++i) {
                bind_slot_to_stmt(stmt, &batch[i]);
                sqlite3_step(stmt);
                sqlite3_reset(stmt);
            }

            // Update checkpoint record
            update_projection_checkpoint(ctx->db, batch[count - 1].feed_sequence);

            // Commit Transaction
            sqlite3_exec(ctx->db, "COMMIT;", NULL, NULL, NULL);

            count = 0;
            last_flush_ts = now;
        } else {
            // Brief pause / yield if queue empty
            thrd_yield();
        }
    }
}
```

---

## 5. Bounded Queue Saturation & Backpressure Policy

A common failure mode in trading architectures is unbounded memory growth when database writes lag behind market data spikes (e.g., market opening auctions or CPI announcements).

MDRAP v5 prevents out-of-memory panics through a **Non-Blocking Fallback Protocol**:
1. **Bounded SPSC Ring**: SPSC queue capacity is strictly bounded at 262,144 slots ($33.55\ \text{MB}$).
2. **Hotpath Drop without Stalling**: If the projection queue fills completely, the hotpath does **not** block. It increments the atomic telemetry counter `stat_projection_queue_drops` and continues processing and writing to `IngestLog`.
3. **Autonomous WAL Catchup**:
   - When the projection worker detects that the queue has dropped frames (detected via gap between `last_projected_seq` and next dequeued sequence), it suspends dequeueing from the SPSC ring.
   - It transitions to **Catchup Mode**: opens the binary `IngestLog`, reads missing frames from disk sequentially, writes them in large batches of 5,000 to SQLite, and then resynchronizes with the live SPSC queue.
4. **Result**: Zero data loss, zero hotpath stalls, and guaranteed bounded RAM usage under extreme market conditions.
