# MDRAP v4.0.1 Projection Specification

**Specification Version:** 4.0.1-PROJ-SPEC  
**Status:** Approved / Enforced  
**Component:** `mdrap.projection.ProjectionTracker`, `mdrap.projection.SQLiteProjection`  

---

## 1. Scope & Architectural Decoupling

Downstream projections (SQLite, Parquet, SHM, Analytics) translate validated canonical events into materialized query stores. This specification establishes strict separation between authoritative ingestion and downstream projection delivery.

### Core Decoupling Invariant
> **The global engine ingestion lock (`Engine._lock`) must never be held during projection delivery, network fan-out, or database disk I/O.**

`Engine.submit()` completes WAL append, atomic state transition, and decision recording under `self._lock`. Once decisions are produced, the engine releases `self._lock` and asynchronously dispatches decisions to registered projections via `ProjectionTracker`.

---

## 2. Checkpoint Monotonicity & Gap Invariant

### 2.1 The Contiguous Checkpoint Rule
A projection checkpoint represents the **highest contiguous offset** durably committed by that projection.
- If offset 2 is committed, and batch [6, 7] arrives while [3, 4, 5] failed or is in retry, the projection checkpoint **must remain at 2**.
- Checkpoint advancement condition:
  $$\text{cur\_checkpoint} < 0 \quad\lor\quad (\text{batch\_end} \ge \text{cur\_checkpoint} \;\land\; \text{batch\_start} \le \text{cur\_checkpoint} + 1)$$
  where $\text{batch\_start} = \text{batch\_end} - |\text{batch}| + 1$.

### 2.2 Replay & Catch-Up Mechanics
When a projection experiences transient failure:
1. Failed offsets are retained in `tracker.failed_offsets`.
2. Telemetry marks `health_status = "DEGRADED"` and exposes `lag = latest_wal_offset - checkpoint`.
3. Background retry coordinator queries the authoritative WAL (`log.iter_from(checkpoint + 1)`) and replays missing frames into the projection.
4. Once contiguous history is written, the checkpoint atomically advances to the head.

---

## 3. SQLite Projection Isolation & Resilience

### 3.1 Concurrency & `SQLITE_BUSY` Handling
- **WAL Mode & Pragmas:** Connections are initialized with:
  ```sql
  PRAGMA journal_mode=WAL;
  PRAGMA synchronous=NORMAL;
  PRAGMA busy_timeout=5000;
  ```
- **External Lock Isolation:** External read/write locks (e.g. backup tools holding table locks) cannot block the engine ingestion stream. The SQLite worker handles `sqlite3.OperationalError` ("database is locked") with bounded exponential backoff (initial: 10ms, max: 500ms, max retries: 5).
- **Atomic Batch & Checkpoint:** Insertion of canonical records and update of `projection_checkpoints` occur within a single SQLite transaction:
  ```sql
  BEGIN IMMEDIATE;
  INSERT OR REPLACE INTO canonical_events (...) VALUES (...);
  INSERT INTO projection_checkpoints (projection_name, checkpoint_offset, updated_at)
  VALUES ('sqlite_canonical', ?, ?)
  ON CONFLICT(projection_name) DO UPDATE SET checkpoint_offset=excluded.checkpoint_offset, updated_at=excluded.updated_at;
  COMMIT;
  ```

---

## 4. Projection Telemetry & Health Contract

Every projection exposes standard observability telemetry:
```json
{
  "name": "sqlite_canonical",
  "checkpoint": 2,
  "latest_wal_offset": 7,
  "lag": 5,
  "failed_offsets": [3, 4, 5, 6, 7],
  "consecutive_failures": 5,
  "retry_count": 5,
  "last_success_time": 1793611624.148,
  "last_failure_time": 1793611624.148,
  "recovery_status": "DEGRADED"
}
```

### Health State Transitions
- **`HEALTHY`:** `lag == 0` and `consecutive_failures == 0`.
- **`DEGRADED`:** `lag > 0` or transient retry in progress.
- **`UNHEALTHY`:** Consecutive failures exceed `max_retries` threshold or queue overflow occurs.
