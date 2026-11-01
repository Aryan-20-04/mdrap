# MDRAP v4.0.0 — Phase 0 Architecture Inventory

## 1. Top-Level Entry Points
- **CLI**: `cli.py` / `src/mdrap/cli/__init__.py` — Rich TUI orchestrator with subcommands: `run`, `bench`, `audit`, `inspect`, `replay`.
- **Daemon / TCP Distribution**: `src/mdrap/service.py` (`MarketDataDaemon`) — Multi-client socket server with TLS option.
- **REST / WebSocket Gateway**: `src/mdrap/api.py` — FastAPI app serving snapshots, book state, and live SSE/WebSocket streams.
- **Ingestion Facades**:
  - `src/mdrap/engine.py` (`Engine`): Canonical log-driven facade; step/submit/replay/snapshot. Primary golden path.
  - `src/mdrap/pipeline.py` (`Pipeline`): Legacy multi-threaded pipeline connecting adapters, validator, reconciler, and SQLite.

## 2. Module Inventory & Call Graph

```
External Feeds (WS / TCP / PCAP)
           │
           ▼
src/mdrap/gateway.py (Normalization & Clock Stamping)
           │
           ▼
src/mdrap/engine.py (Authoritative Transaction Boundary)
   ├── Stage 1: Payload Size Gate
   ├── Stage 2: IngestLog WAL Append (src/mdrap/ingestlog.py)
   ├── Stage 3: Clock & Session Boundary
   ├── Stage 4: Sequence & Dedup (src/mdrap/gap.py)
   ├── Stage 5: Staleness & Crossed Quotes
   ├── Stage 6: Statistical Price Corridor (Welford)
   ├── Stage 7: BBO & Multi-Feed Arbitration (src/mdrap/reconciliation.py)
   └── Stage 8: Projection Dispatch (src/mdrap/projection.py)
```

## 3. Parallel Paths & Source-of-Truth Assessment
- **Dual Pipeline Issue**: `Engine` in `src/mdrap/engine.py` implements its own internal stages (`_stage_sequence_and_dedup`, `_stage_price_corridor`, `_stage_bbo_and_reconciliation`), while `src/mdrap/quality.py` and `src/mdrap/pipeline.py` implement parallel versions of the same checks.
- **Source of Truth Target**: `Engine` must be established as the single authoritative state fold, with `quality.py` acting as pure rule definitions reused by both `Engine` and native C fastpath (`src/fastpath.c`).
