# MDRAP Project Rules

## Project Context
This is the **Market Data Reliability & Acceleration Platform** — an institutional market-data validation, normalization, reconciliation, and audit sidecar designed to convert noisy, delayed, duplicated, and inconsistent market data from multiple feeds into a fast, validated, canonical stream. It sits before trading strategies, analytics, and downstream databases. The reference spec is `Market_Data_Reliability_Acceleration_Platform_Reference.docx`.

## Architecture & Tiers
- **Pipeline Flow**: `feed/simulator → gateway.ingest → gateway.normalize → quality.evaluate → reconciler.reconcile → storage (SQLite, batched)`.
- **Tiers & Acceleration**:
  - Pure Python + stdlib baseline with 100% semantic parity across all platforms.
  - Native C hot path kernel (`src/fastpath.c`, `src/mdrap_core.c`) for high-throughput SBE decoding, seqlock SHM ring buffers, and bounded-memory dedup.
  - POSIX / Windows Shared Memory (`src/shm.py`) for low-latency IPC stream distribution.
  - Optional API server (`src/api.py`) exposing REST & WebSocket endpoints via FastAPI/Uvicorn.
  - CLI entry point (`cli.py`) orchestrating all subcommands.
- All core engine modules reside in `src/`. Tests in `tests/`. Benchmarks in `benchmarks/`.

## Design Principles
1. **Correctness before optimization** — never sacrifice validation accuracy for speed.
2. **Measure before claiming** — every benchmark number must trace to reproducible timed runs.
3. **Never silently discard bad data** — quarantine INVALID events; record and expose drop/eviction counters.
4. **Pure Python Fallback Parity** — pure Python must match native C validation outputs exactly.
5. **Separate raw, processed, and derived data**.
6. **Preserve lineage** — every canonical value traces back to its source.
7. **Tail latency matters** — report p50/p95/p99/p99.9, not just averages.
8. **A real market anomaly is not automatically a data error** — SUSPICIOUS ≠ INVALID.
9. **Deterministic experiments** — fixed seeds (`seed=42`), reproducible results.
10. **Quality status has strict priority**: INVALID > SUSPICIOUS > VALID. Never downgrade.

## Coding Standards
- Core engine is pure Python, stdlib-only where possible. Only external dependency for core CLI is `rich`.
- Dataclasses for models, no ORM, no heavyweight framework in core.
- Batch SQLite writes via `executemany` with WAL mode.
- Use `itertools.count()` for monotonic IDs in hot paths, avoiding `uuid.uuid4()`.
- Explicit error handling: never use bare `except: pass` in hot paths or network/IPC handlers — log debug/warning and update failure counters.
- Type hints on public APIs (`list[str]`, `dict[str, Any]`).

## Testing & Quality Assurance
- `pytest tests/ -v` must pass cleanly before any commit or release.
- Tests support both native compiled C acceleration and `$env:MDRAP_DISABLE_FASTPATH="1"` pure-Python fallback.
- Security defaults: permissions on SHM set to 0600, API keys hashed with salt, explicit exception raising when secrets cannot be loaded.
