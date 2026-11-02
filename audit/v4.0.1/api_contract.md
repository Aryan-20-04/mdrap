# MDRAP v4.0.1 API Contract

**Package:** `mdrap`  
**Version:** 4.0.1  
**Component Interfaces:** Engine, IngestLog, Projections, State Models  

---

## 1. Engine (`mdrap.engine.Engine`)

### 1.1 Initialization
```python
Engine(
    path: str | Path | None = None,
    config: EngineConfig | None = None,
    clock: Clock | None = None,
    staleness_threshold_s: float = 0.05,
    session_policy: str = "auto_detect", # "strict" | "session_marker" | "auto_detect"
    max_payload_bytes: int = 1_048_576,
    on_gap: Callable[[str, str, int, int], None] | None = None,
    projections: list[Any] | None = None,
    fsync_policy: str = "grouped_by_time",
    max_snapshots_retained: int = 5,
)
```

### 1.2 Core Methods
- `submit(events: RawEvent | list[RawEvent]) -> EngineDecision | list[EngineDecision]`
  - Validates payload size and serializability.
  - Appends valid raw events to WAL.
  - Updates engine state within an isolated atomic transaction.
  - Releases ingestion lock before dispatching to downstream projections.
  - Returns decisions with monotonic WAL `offset` (or `-1` if rejected at boundary).
- `save_snapshot() -> dict[str, Any]`
  - Synchronously freezes atomic state and saves cryptographically verified `snapshot_{offset:016d}.json`.
  - Returns `{"path": str, "snapshot_offset": int, "event_count": int, "sha256": str, "duration_ms": float}`.
- `await_durable(target: int | EngineDecision, timeout: float = 2.0) -> bool`
  - Blocks until specified offset or decision is committed to physical storage (`fsync`).
- `delete_segments_before(offset: int) -> int`
  - Safely deletes WAL segments containing only records strictly prior to `offset`.
- `replay(from_offset: int = 0, to_offset: int | None = None) -> list[EngineDecision]`
  - Replays historical WAL log without triggering external network/gap side-effects.

---

## 2. IngestLog (`mdrap.ingestlog.IngestLog`)

### 2.1 Methods
- `append(raw: RawEvent | dict) -> int`
  - Appends single record with CRC32 framing. Returns assigned WAL offset.
- `append_batch(batch: Sequence[RawEvent | dict]) -> list[int]`
  - Atomically appends batch of records. Rollbacks cleanly on write error.
- `flush() -> None`
  - Flushes stdio buffers and calls `os.fsync()`.
- `seek_record(offset: int) -> tuple[int, RawEvent] | None`
  - Seeks directly to record by offset using segment index.
- `iter_from(start_offset: int = 0) -> Iterator[tuple[int, RawEvent]]`
  - Yields records starting at `start_offset`. Raises `RecoveryError` if history prior to requested start is pruned.

---

## 3. SQLiteProjection (`mdrap.projection.SQLiteProjection`)

### 3.1 Methods
- `apply(decisions: list[EngineDecision], raw_events: list[RawEvent]) -> None`
  - Applies batch of decisions within `BEGIN IMMEDIATE` transaction.
  - Enforces contiguous checkpoint advancement.
- `get_checkpoint() -> int`
  - Returns last committed contiguous offset.
- `metrics() -> dict[str, Any]`
  - Returns checkpoint, lag, error count, and recovery status.

---

## 4. Exceptions Hierarchy

- `MDRAPError`: Base platform exception.
  - `RecoveryError`: Raised when engine recovery cannot reconstruct authoritative state due to corrupted or missing WAL history.
  - `IngestLogCorruptError`: Raised on torn write or CRC32 mismatch.
  - `SchemaViolationError`: Raised on oversized or non-serializable payloads.
