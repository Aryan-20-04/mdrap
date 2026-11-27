# MDRAP Observer

**Institutional Read-Only Market Data Cockpit and Multi-Venue Integrity Monitor**

MDRAP Observer is an independent downstream sidecar application that monitors real-time market data across 5 tier-1 crypto and institutional feeds (Binance, Kraken, Coinbase, Databento, ITCH/SBE) for core assets (BTC, ETH).

It connects exclusively to MDRAP's certified public interfaces (`StreamClient` and `SHMReader`) and enforces a strictly read-only architecture with zero execution paths.

---

## Architecture & Features

- **Public API Exclusivity**: Uses only `mdrap.shm.SHMReader`, `mdrap.shm.ReaderState`, and `mdrap.service.StreamClient`. No internal modules are imported.
- **5-Venue Feed Matrix**: Tracks prices, spreads, sequence gaps, and feed health across:
  - `BINANCE`
  - `KRAKEN`
  - `COINBASE`
  - `DATABENTO`
  - `ITCH_SBE`
- **Channel & Memory Bounds**: Bounded quote/latency windows keep per-asset buffers fixed-size, while `VenueHealth.channels` grows as `O(C)` with observed distinct gateway/channel/asset pairs.
- **Honest Telemetry Attribution**: Stream-level reconnects and exporter drops are reported at stream/exporter scope (`transport_reconnects`, `exporter_dropped_records`); `VenueHealth.drops` and `VenueHealth.reconnects` remain `None` (unsupported at per-venue scope) rather than reporting synthetic zeros.
- **Consolidated NBBO**: Calculates Best Bid, Best Offer, Mid Price, MicroPrice, and Bid-Ask Spread (bps).
- **Trade Aggressor Classification (Heuristic)**: Infers `BUY` / `SELL` trade direction using a Lee-Ready heuristic (comparing trade price to current top-of-book bid/ask with a tick-rule fallback). Because upstream feeds may omit explicit aggressor flags, this classification is a **heuristic estimate** rather than exchange-certified execution side.
- **Resilient Reconnection**: Automatically recovers from network disconnects, daemon restarts, and shared memory ring re-initialization, with active `STALE` venue detection (`check_all_stale()`).
- **JSONL Data Export**: Asynchronously writes raw events and periodic system health snapshots (including transport `stream` continuity, per-venue feed diagnostics, and exporter counters) without blocking the rendering loop.
- **Terminal UI Cockpit**: In-place Rich terminal dashboard rendering at up to 10 Hz with zero flicker.

---

## Exporter Durability Contract

`ObserverExporter` writes `events_<session_id>.jsonl` and `health_<session_id>.jsonl` from a dedicated background worker thread:

- **Clean Shutdown (`stop(timeout_s=30.0, raise_on_timeout=False)`)**: Stops accepting new records, waits up to `timeout_s` for the worker to drain all queued items, flushes both streams (and executes `os.fsync(fp.fileno())` when constructed with `fsync_on_flush=True`), closes all file handles, and returns a structured status summary (`drained`, `timed_out`, `remaining_queue`, `abandoned_on_timeout`, `written_records`, `flushed_records`, `fsynced_records`, `failed_records`, `dropped_records`, `write_errors`). If `timeout_s` expires before drain finishes, `stop()` marks `incomplete_shutdown=True`, logs an error, records `abandoned_on_timeout`, and optionally raises `TimeoutError` when `raise_on_timeout=True`.
- **Abrupt Termination (`SIGKILL`, power loss, hard crash)**: **NOT crash-durable** for in-queue or OS-buffered records. Even with `fsync_on_flush=True`, any records still waiting in the in-memory queue at crash time are lost, and records written between periodic flushes remain subject to OS page-cache buffering until flushed/fsynced.
- **Bounded Queue Backpressure**: Requires a strictly positive bounded `queue_size` (default `10000`, raising `ValueError` for `queue_size <= 0`). When full, excess records are dropped immediately without blocking ingress and counted via `exporter.dropped_records`.
- **I/O Error Resilience**: Open, write, flush, fsync, and close `OSError` failures are caught, logged, and counted via `exporter.write_errors` / `exporter.failed_records` without crashing the caller, advancing `flushed`/`fsynced` prematurely, or leaking file handles.

---

## Installation

Install MDRAP Observer alongside MDRAP Core:

```bash
pip install mdrap_core-6.0.8-py3-none-any.whl
cd external_sidecar/mdrap_observer
pip install -e .
```

---

## Usage

### 1. TCP Mode (Standard Streaming Daemon)

Ensure the MDRAP streaming daemon is active (`mdrap daemon --port 9000`), then launch Observer:

```bash
python -m observer.cli --mode tcp --host 127.0.0.1 --port 9000
```

### 2. SHM Mode (Ultra-Low Latency Lock-Free IPC)

When consuming directly from the local shared memory ring buffer:

```bash
python -m observer.cli --mode shm --ring mdrap_canonical_ring
```

### 3. Headless Exporter Mode (Automated Soak / Telemetry)

To run without rendering the UI (ideal for background telemetry collection):

```bash
python -m observer.cli --mode tcp --no-ui --export ./soak_logs --duration 300
```

---

## Governance Notice

> [!IMPORTANT]
> **NO DEPLOYMENT**
> MDRAP Observer is strictly a read-only monitoring and analytical tool. It contains zero order routing, strategy triggers, or account credentials.
