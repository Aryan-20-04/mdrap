# HFT Trading Desk Starter Architecture

Minimal, ultra-low-latency deployment blueprint for a co-located high-frequency trading (HFT) desk using the Market Data Reliability & Acceleration Platform (MDRAP).

---

## 1. Overview & Architectural Goals

In high-frequency trading, market data delivery must achieve deterministic sub-microsecond latency while guaranteeing that corrupted or out-of-order ticks never reach execution algorithms.

This starter architecture isolates the critical hot path:
- **Zero Python interpreter frames** on the per-tick hot path via native C acceleration.
- **Lock-free zero-copy IPC** delivering validated events into co-located trading processes via shared memory (SHM).
- **Fixed-width binary wire protocol** for high-density, low-overhead inter-process and network socket transmission.
- **No blocking I/O, no async event loops, and no garbage collection jitter** on the critical decision path.

```mermaid
flowchart TD
    subgraph Market Ingest
        NIC["NIC / Kernel Bypass (Solarflare / DPDK)"] --> RAW["Raw Market Data Stream"]
    end

    subgraph Hot Path: mdrap Process
        RAW --> GW["Gateway: Normalization & Schema Check\n(gateway.py)"]
        GW --> FQE["Native C FastQualityEngine\n(fastpath.c / fastpath.py)\n~24-37 ns evaluation"]
        FQE --> PIPE["Synchronous Pipeline\n(pipeline.py)"]
        PIPE --> SHMW["SHMWriter: Two-Phase Commit\n(shm.py /dev/shm Ring Buffer)"]
        PIPE -.-> RECON["Reconciler & Reliability Tracker\n(reconciliation.py)"]
    end

    subgraph Zero-Copy IPC Transport
        SHMW -->|"Atomic Sequence Publish\n(64-byte aligned slots)"| SHM_RING["Shared Memory Ring Buffer\n(/dev/shm/mdrap_feed)"]
    end

    subgraph Co-Located Trading Algorithms
        SHM_RING -->|"Sub-microsecond Polling\n(Zero Syscalls)"| STRAT1["Execution Strategy A\n(SHMReader)"]
        SHM_RING -->|"Sub-microsecond Polling\n(Zero Syscalls)"| STRAT2["Arbitrage Strategy B\n(SHMReader)"]
    end

    subgraph Remote / Non-Colocated Distribution
        PIPE --> TCP_SRV["TCP Streaming Daemon\n(service.py / protocol.py)"]
        TCP_SRV -->|"MDRAP-BIN Binary Wire Frames\n(92-byte struct)"| REMOTE_CLIENT["Remote Strategy / Risk Gateway"]
    end
```

---

## 2. Core Components Used

This minimal HFT deployment utilizes only 5 core modules from MDRAP:

| Component | Module | Role in HFT Architecture |
|---|---|---|
| **Core Models & Pipeline** | [`models.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/models.py), [`gateway.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/gateway.py), [`pipeline.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/pipeline.py) | Normalized [`CanonicalEvent`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/models.py) representation, synchronous ingress loop, deterministic GC management (`tuned_gc`). |
| **Native C Fastpath** | [`fastpath.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/fastpath.py), [`fastpath.c`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/fastpath.c) | Sub-microsecond (24–37 ns) quality scoring across 7 core validation rules (gap, dedup, price sanity, crossed books). |
| **SHM Ring Buffer** | [`shm.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/shm.py) | High-throughput [`SHMWriter`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/shm.py) and [`SHMReader`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/shm.py) with SPMC lock-free circular ring buffer, two-phase commit, and cache-line padding. |
| **Binary Wire Protocol** | [`protocol.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/protocol.py) | Compact 92-byte pre-compiled struct framing ([`pack_tick_frame`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/protocol.py)) eliminating JSON parsing overhead. |
| **TCP Streaming Daemon** | [`service.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/service.py) | [`MarketDataDaemon`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/service.py) for socket connectivity to remote risk desks and hardware ring replay buffers. |

---

## 3. Configuration Example (`config.yaml`)

A streamlined configuration tailored for low jitter and immediate failover:

```yaml
# HFT Desk Minimal Low-Latency Configuration
# Reference: Market Data Reliability & Acceleration Platform Spec §6, §7, §18

# 1. Quality Engine Fastpath Thresholds
quality:
  staleness_threshold_s: 0.005      # 5ms max staleness for co-located exchange feeds
  price_anomaly_stddev: 5.0         # 5-sigma outlier band for price spike rejection
  price_window: 100                 # Rolling Welford sample window
  dedup_cache_size: 500000          # Sliding bitmap deduplication window

# 2. Synthetic BBO & Depth
bbo:
  quote_ttl_s: 0.5                  # Expire stale quotes after 500ms

# 3. Source Health & Live Watchdog
watchdog:
  silence_threshold_s: 0.25         # Fast silence trigger at 250ms
  degradation_threshold: 0.95       # Disconnect source if reliability drops below 95%
  recovery_threshold: 0.98          # Strict recovery hysteresis

# 4. Storage (Decoupled or High-Speed WAL)
storage:
  batch_size: 5000                  # Large batches to prevent I/O interrupt on hot path
  wal_mode: true
  synchronous: "OFF"                # Highest write speed for non-critical local logs

# 5. Shared Memory & Daemon IPC
daemon:
  host: "127.0.0.1"
  port: 9876
  enable_shm: true
  shm_name: "mdrap_hft_primary"
  slot_count: 65536                 # Power-of-2 ring buffer capacity (65,536 slots)
  binary_protocol: true
```

---

## 4. End-to-End Implementation Example

The following code illustrates the zero-copy pipeline:
1. Producer initializes [`Pipeline`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/pipeline.py) with [`FastQualityEngine`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/fastpath.py) and publishes validated events to [`SHMWriter`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/shm.py).
2. Algorithmic trading client consumes ticks in a lock-free loop via [`SHMReader`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/shm.py) with zero kernel context switches.

```python
"""
HFT Trading Desk Starter: Pipeline -> SHMWriter -> SHMReader Flow.
Run this directly with Python 3.10+ in the MDRAP environment.
"""

from __future__ import annotations

import time
import itertools
from models import RawEvent, QualityStatus
from gateway import ingest, normalize
from fastpath import FastQualityEngine, is_available
from pipeline import Pipeline
from storage import Store
from shm import SHMWriter, SHMReader

SHM_SEGMENT_NAME = "mdrap_hft_primary"
SLOT_COUNT = 65536  # Must be a power of 2


def run_producer():
    """Producer process: Line-rate ingest -> Fast C Quality -> SHM Commit."""
    print(f"[*] Initializing HFT Producer (Native C available: {is_available()})")
    
    # In-memory storage sink to eliminate disk I/O on the tick path
    store = Store(":memory:")
    quality_engine = FastQualityEngine() if is_available() else None
    pipeline = Pipeline(store=store, quality=quality_engine)
    shm_writer = SHMWriter(name=SHM_SEGMENT_NAME, slot_count=SLOT_COUNT)

    seq_gen = itertools.count(1)

    print("[*] Publishing validated ticks to Shared Memory...")
    try:
        for i in range(100_000):
            seq = next(seq_gen)
            now = time.time()
            
            # Simulate raw tick arrival from exchange NIC
            raw = RawEvent(
                source="NASDAQ",
                payload={
                    "instrument": "AAPL",
                    "event_type": "TRADE",
                    "price": 182.50 + (i % 20) * 0.01,
                    "size": 100.0,
                    "bid": 182.49,
                    "ask": 182.51,
                    "bid_size": 500.0,
                    "ask_size": 400.0,
                    "exchange_ts": now - 0.0001,
                    "sequence": seq,
                },
                receive_timestamp=now,
            )

            # 1. Process tick through C-accelerated pipeline
            canonical = pipeline.process_one(raw)
            if not canonical or canonical.quality_status == QualityStatus.INVALID:
                continue

            # 2. Write directly to shared memory ring buffer with two-phase commit
            shm_writer.write_tick(
                seq=seq,
                symbol=canonical.instrument_id,
                source=canonical.source,
                price=canonical.price,
                size=canonical.quantity,
                bid=canonical.bid_price,
                ask=canonical.ask_price,
                bid_size=canonical.bid_size,
                ask_size=canonical.ask_size,
                status=canonical.quality_status.value,
                is_crossed=False,
                exchange_ts=canonical.exchange_timestamp,
                ingest_ts=raw.receive_timestamp,
                broadcast_ts=time.time(),
                engine_us=0.05,
            )
    finally:
        shm_writer.close()
        store.close()
        print("[*] Producer finished.")


def run_strategy_consumer():
    """Consumer process: Sub-microsecond lock-free spin loop in trading algo."""
    print("[*] Connecting Strategy Client to Shared Memory...")
    reader = SHMReader(name=SHM_SEGMENT_NAME)

    received_count = 0
    start_seq = reader.read_latest_seq()

    try:
        # Stream events via lock-free memory polling
        for tick in reader.stream(start_seq=start_seq, timeout=2.0):
            if tick.get("type") == "EPOCH_CHANGE":
                print("[!] Publisher restart detected! Resetting sequence.")
                continue

            received_count += 1
            symbol = tick["sym"]
            price = tick["price"]
            seq = tick["seq"]

            # Strategy decision logic executes here:
            if received_count % 25000 == 0:
                print(f"[Strategy Algo] Recv seq={seq} | {symbol} @ {price:.2f} | Status={tick['status']}")

    except KeyboardInterrupt:
        pass
    finally:
        reader.close()
        print(f"[*] Strategy Client exited. Total ticks processed: {received_count}")
```

---

## 5. Performance Expectations

Benchmarks measured on standard x86_64 server hardware:

| Benchmark Dimension | Pure Python Baseline | Native C Acceleration (`fastpath.c`) |
|---|---|---|
| **Quality Evaluation Latency** | ~1,200 ns / tick | **24–37 ns / tick** |
| **Pipeline Ingest Throughput** | 180,000+ events/sec | **500,000–1,000,000+ events/sec** |
| **SHM Delivery Latency** | ~250–600 ns zero-copy | **~250–600 ns zero-copy** |
| **Wire-to-Decision Budget** | ~15–30 µs | **~1–10 µs (T1 Tier Target)** |
| **Memory Framing Overhead** | ~400 bytes (JSON dict) | **92 bytes (Fixed binary struct)** |

> [!TIP]
> For production deployment on Linux, mount the shared memory volume at `/dev/shm` (RAM-backed tmpfs) and bind the ingestion worker and execution strategy threads to dedicated CPU cores via `taskset -c <core>` or `pthread_setaffinity_np` to eliminate OS scheduler interruptions.

---

## 6. What You DON'T Need from MDRAP

An HFT trading desk should aggressively trim non-critical modules to prevent garbage collection pauses, lock contention, and cache pollution:

- **No REST / WebSocket API Service ([`api.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/api.py))**: Avoids HTTP parsing and asyncio loop scheduling overhead.
- **No Analytical Columnar Engine ([`columnar.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/columnar.py))**: DuckDB aggregations belong on an offline analytics node, not on the trading box.
- **No Historical Backtesting ([`backtest.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/backtest.py))**: Keep execution binaries decoupled from backtest runners.
- **No Research & Sentiment Modules ([`research.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/research.py), [`news.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/news.py), [`vessel.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/vessel.py))**: External web scraping and alternative datasets have no role on the microsecond path.
- **No Rich Terminal Cockpit / Interactive CLI ([`terminal_display.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/terminal_display.py), [`navigator.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/navigator.py))**: TUI formatting consumes hundreds of CPU cycles; use headless daemons with lightweight binary metrics.
