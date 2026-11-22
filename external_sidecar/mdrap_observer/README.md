# MDRAP Observer

**Institutional Read-Only Market Data Cockpit and Multi-Venue Integrity Monitor**

MDRAP Observer is an independent downstream sidecar application that monitors real-time market data across 5 tier-1 crypto and institutional feeds (Binance, Kraken, Coinbase, Databento, ITCH/SBE) for core assets (BTC, ETH).

It connects exclusively to MDRAP's certified public interfaces (`StreamClient` and `SHMReader`) and enforces a strictly read-only architecture with zero execution paths.

---

## Architecture & Features

- **Public API Exclusivity**: Uses only `mdrap.shm.SHMReader` and `mdrap.service.StreamClient`. No internal modules are imported.
- **5-Venue Feed Matrix**: Tracks prices, spreads, sequence gaps, drops, and feed health across:
  - `BINANCE`
  - `KRAKEN`
  - `COINBASE`
  - `DATABENTO`
  - `ITCH_SBE`
- **Consolidated NBBO**: Calculates Best Bid, Best Offer, Mid Price, MicroPrice, and Bid-Ask Spread (bps).
- **Resilient Reconnection**: Automatically recovers from network disconnects, daemon restarts, and shared memory ring re-initialization.
- **JSONL Data Export**: Asynchronously writes raw events and periodic system health snapshots without blocking the rendering loop.
- **Terminal UI Cockpit**: In-place Rich terminal dashboard rendering at up to 10 Hz with zero flicker.

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
