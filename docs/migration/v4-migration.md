# MDRAP v4.0 Migration and Deprecation Guide

This guide documents the architectural shift, deprecation policy, and migration path for upgrading institutional deployments from legacy MDRAP (v2.x / v3.x) to MDRAP v4.0.0.

---

## 1. Deprecation Policy

Under MDRAP's institutional stability guarantee:
1. **Deprecation Phase (v3.0.0 – v4.x)**:
   - Deprecated classes (`Pipeline`, `RawArchive`, `BinaryJournal`, `MDRAPClient`) remain fully importable.
   - Emits a standard `DeprecationWarning` pointing to modern replacements.
   - Thin backward-compatibility wrappers guarantee zero regression for legacy scripts.
2. **End-of-Life (v5.0.0)**:
   - Deprecated shims will be removed entirely in v5.0.0.

---

## 2. Architectural Evolution

| Aspect | Legacy Architecture (v2 / v3) | Modern Architecture (v4.0) |
|---|---|---|
| **Durability Boundary** | Ad-hoc SQLite commit / raw JSON archive | Append-only `IngestLog` WAL with microsecond fsync / group commit |
| **State & Step Model** | Monolithic `Pipeline` coordinating threads | Pure deterministic state machine: `state, decision = engine.step(state, raw)` |
| **Downstream Stores** | Direct, synchronous SQLite write locks | Ephemeral, rebuildable materialized projections (`SQLiteProjection`, `DuckDBProjection`) |
| **Persistence Isolation** | Database contention stalls ingestion | Analytical projections isolated behind non-blocking `AsyncProjectionWorker` |
| **Stream Distribution** | Thread-per-client TCP fan-out | Seqlock zero-copy Shared Memory (`shm.py`) and authenticated mTLS streaming |

---

## 3. Migration Paths

### 3.1 Migrating from `Pipeline` to `Engine`

#### Legacy (v2 / v3):
```python
from mdrap.pipeline import Pipeline
from mdrap.storage import Store

store = Store("data/mdrap.db")
pipeline = Pipeline(store=store)

for raw in feed.generate():
    pipeline.process_one(raw)
pipeline.finish()
```

#### Modern (v4.0):
```python
from mdrap.engine import Engine
from mdrap.ingestlog import IngestLog
from mdrap.projection import SQLiteProjection, AsyncProjectionWorker

# IngestLog WAL is the immutable ground truth
log = IngestLog("data/wal")
engine = Engine()
state = engine.create_initial_state()

# Downstream SQLite projection with background worker
projection = SQLiteProjection("data/mdrap.db")
worker = AsyncProjectionWorker(projection=projection)

for raw in feed.generate():
    offset = log.append(raw)
    state, decision = engine.step(state, raw, engine.clock, offset=offset)
    worker.enqueue(decision, offset=offset)

worker.flush()
worker.close()
log.close()
```

---

### 3.2 Migrating from `RawArchive` and `BinaryJournal` to `IngestLog`

Legacy `RawArchive` (JSONL) and `BinaryJournal` (.dbn fixed binary) are replaced by the unified, CRC32c-framed, variable-length `IngestLog`:

```python
from mdrap.ingestlog import IngestLog

# Open WAL directory with segment size limits
log = IngestLog(
    journal_dir="data/wal",
    max_segment_bytes=64 * 1024 * 1024,
    sync_policy="grouped_by_time",
)

# Append raw wire event
offset = log.append(raw_event)

# Replay deterministically from offset
for offset, raw in log.iter_from(0):
    process_replayed_tick(offset, raw)
```

---

### 3.3 Rebuilding Downstream Projections from WAL

Because the WAL is the single source of truth, if the SQLite database is corrupted, deleted, or missing historical data, it can be completely rebuilt without stopping ingestion:

```python
from mdrap.projection import SQLiteProjection, rebuild_projection_from_wal

proj = SQLiteProjection("data/rebuilt.db")
total_rebuilt = rebuild_projection_from_wal(
    journal_dir="data/wal",
    projection=proj,
    start_offset=0,
    batch_size=2000,
)
print(f"Successfully reconstructed {total_rebuilt} events from WAL.")
```

Via CLI:
```bash
mdrap replay --wal-dir data/wal --db data/mdrap.db
```
