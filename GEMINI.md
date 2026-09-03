# MDRAP Project Rules

## Project Context
This is the **Market Data Reliability & Acceleration Platform** — a financial-market infrastructure system for converting noisy, delayed, duplicated, inconsistent market data from multiple sources into a fast, validated, canonical real-time data stream. The reference spec is `Market_Data_Reliability_Acceleration_Platform_Reference.docx`.

## Architecture
- **V1 baseline**: Single-process synchronous Python pipeline. No broker, no async, no web UI.
- Entry point: `cli.py` → dispatches to `src/` modules.
- Pipeline: `simulator → gateway.ingest → gateway.normalize → quality.evaluate → reconciler.reconcile → storage (SQLite, batched)`.
- All modules are in `src/`. Tests in `tests/`. Benchmarks in `benchmarks/`.

## Design Principles (from spec section 26)
1. **Correctness before optimization** — never assume faster is correct.
2. **Measure before claiming** — every perf number must come from a timed run.
3. **Never silently discard bad data** — quarantine, never drop.
4. **Separate raw, processed, and derived data**.
5. **Preserve lineage** — every canonical value traces back to source.
6. **Tail latency matters** — report p50/p95/p99/p99.9, not just averages.
7. **A real market anomaly is not automatically a data error** — SUSPICIOUS ≠ INVALID.
8. **Every optimization must be regression-tested for correctness**.
9. **Deterministic experiments** — fixed seeds, reproducible results.
10. **Benchmark results decide the winning architecture**, not assumptions.

## Coding Standards
- Pure Python, stdlib-only where possible. Only external dep: `rich` for terminal UI.
- Dataclasses for models, no ORM, no framework.
- Batch SQLite writes via `executemany` — per-row commits are the known bottleneck.
- Use `itertools.count()` for IDs, not `uuid.uuid4()` (crypto entropy is too slow for hot paths).
- Quality status has strict priority: INVALID > SUSPICIOUS > VALID. Never downgrade.
- `Store` supports context manager (`with Store(...) as s:`). Always use `try/finally` for cleanup.
- Type hints on public APIs. `list[str]` over `List[str]`.

## Testing
- `pytest tests/ -v` — must pass before any PR/commit.
- Tests use `:memory:` SQLite and deterministic seeds.
- Quality detection is scored against known ground truth from the simulator.
- Every benchmark result is JSON and stored in `benchmarks/`.

## What NOT to Do
- Don't add async/threading to the pipeline — it's deliberately synchronous for V1 baseline measurement.
- Don't add HTTP/REST/WebSocket — CLI-only for MVP; FastAPI is planned for later.
- Don't add Kafka/Redpanda — that's V2.
- Don't use `uuid.uuid4()` in hot paths.
- Don't silently drop events — always quarantine INVALID events.
