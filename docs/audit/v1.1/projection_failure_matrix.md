# MDRAP v4.0.0 — Phase 0 Projection Failure & Idempotency Matrix

## Projection Matrix

| Projection | Delivery Semantics | Failure Handling | Idempotency Key | Checkpoint Storage |
|:---|:---:|:---|:---|:---|
| **SQLite Projection** (`src/mdrap/projection.py`) | At-least-once | Local transaction retry; backpressure queue | `UNIQUE(offset)` or `raw_id` | `_mdrap_checkpoints(projection_name, last_offset)` table |
| **SHM Ring Buffer** (`src/mdrap/shm.py`) | Best-effort / At-most-once | Overwrite on slow reader; seqlock version bump | Monotonic sequence slot | Header `write_seq` |
| **WebSocket / SSE Stream** (`src/mdrap/api.py`) | At-most-once | Drop on slow client queue full | Connection epoch + frame sequence | In-memory socket buffer |
| **Columnar / DuckDB** (`src/mdrap/columnar.py`) | At-least-once | Batch retry from last committed WAL segment | Segment file hash + start offset | Metadata table |

## Failure Boundary Invariants
1. A failure in any projection MUST NOT roll back `EngineState` or corrupt the authoritative WAL log.
2. Ingest transaction commits immediately upon WAL write/sync. Projections consume asynchronously or in an isolated post-commit pipeline.
3. Replying from a checkpoint must be safe: downstream database writes use `INSERT OR REPLACE` / `ON CONFLICT DO UPDATE`.
