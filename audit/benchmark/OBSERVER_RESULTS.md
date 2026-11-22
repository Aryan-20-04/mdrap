# MDRAP v6.0.8 — Observer Sidecar Benchmark Results

**Audit Timestamp**: 2026-11-21  
**Target Environment**: Windows 11 AMD64 (12 logical cores, Intel Core @ 1.70 GHz, 15.72 GB RAM), Python 3.13.1  
**Git Commit**: `51f99e8e18768860540b534297b02b507cefce05` (`main`)  
**Data Sources**: `audit/benchmark/raw/observer_benchmark.json`

---

## 1. Executive Summary: Observer Sidecar Performance

The `mdrap_observer` sidecar (`external_sidecar/mdrap_observer/`) was independently evaluated across:
1. **In-Memory State Engine (`ObserverState`)**: Evaluates quote books, micro-price updates, and multi-domain sequence accounting across 5 independent venue adapters (`BINANCE`, `KRAKEN`, `COINBASE`, `DATABENTO`, `ITCH_SBE`) and 4 core assets (`BTC-USD`, `BTC-USDT`, `ETH-USD`, `ETH-USDT`).
2. **Asynchronous JSONL Exporter (`ObserverExporter`)**: Background non-blocking worker persisting structured JSONL events and health telemetry with bounded queues.
3. **Queue Saturation & Drop Accounting**: Verifies graceful degradation and strict drop tracking under sudden load spikes without blocking or unbounded memory leaks.

### Key Performance Findings
- **In-Memory Processing Throughput**: Sustains **154,156.4 EPS** across 5 venues and 4 instruments with sub-microsecond latency: **5.2 µs (p50)** and **14.9 µs (p99)**.
- **Asynchronous Exporter Ingestion Rate**: Enqueues incoming records at **559,684.8 records/sec** into bounded memory queues.
- **Sustained Disk Drain Throughput**: Drains and persists JSONL events to disk at **63,141.1 records/sec** with zero write errors.
- **Deterministic Drop Accounting**: Under a 10,000-event burst against a 2,000-slot queue, exactly 2,000 records were written and exactly 8,000 drops were accounted for (**100.0% accounting precision**).
- **Strictly Bounded Memory**: Net memory growth after processing 100,000 events across 20 symbol-venue pairs was only **5.21 MB**.

---

## 2. In-Memory State Engine Benchmark (100,000 Events)

Workload: 100,000 synthetic market data events interleaved across 5 venue adapters and 4 core crypto instruments.

| Metric | Measured Value | Operational SLA | Status |
|:---|:---:|:---:|:---:|
| **Total Events Processed** | **100,000** | 100,000 | PASS |
| **Elapsed Wall Time** | **0.649 s** | < 2.0 s | PASS |
| **Throughput (EPS)** | **154,156.4 EPS** | > 50,000 EPS | **PASS (3.08x SLA)** |
| **CPU Time** | **0.61 s** | — | High Efficiency |
| **Processing Latency p50** | **5.2 µs** | < 25.0 µs | **PASS** |
| **Processing Latency p90** | **8.7 µs** | < 50.0 µs | **PASS** |
| **Processing Latency p95** | **10.5 µs** | < 100.0 µs | **PASS** |
| **Processing Latency p99** | **14.9 µs** | < 250.0 µs | **PASS** |
| **Processing Latency p99.9** | **24.3 µs** | < 500.0 µs | **PASS** |
| **Processing Latency Max** | **4,250.2 µs** | — | GC / OS Jitter |
| **Initial Process RSS** | 56.42 MB | — | Baseline |
| **Final Process RSS** | 61.63 MB | — | Bounded |
| **Net RSS Growth** | **+5.21 MB** | < 20.0 MB | **PASS** |
| **Active Venue Tracks** | 5 / 5 | 5 | All Venues Tracked |
| **Active Symbol Books** | 4 / 4 | 4 | All Books Maintained |
| **Stream Sequence Gaps** | **0** | 0 | Clean Stream |
| **Feed Sequence Gaps** | **0** | 0 | Zero Phantom Gaps |

---

## 3. Asynchronous JSONL Exporter Benchmark (50,000 Records)

Workload: 50,000 market tick records submitted to `ObserverExporter` with `flush_interval_s=0.20`.

| Metric | Measured Value | Operational SLA | Status |
|:---|:---:|:---:|:---:|
| **Total Records Enqueued** | **50,000** | 50,000 | PASS |
| **Producer Enqueue Elapsed** | **0.089 s** | < 0.5 s | **PASS** |
| **Producer Enqueue Rate** | **559,684.8 records/s** | > 100,000 records/s | **PASS** |
| **Total Disk Drain Elapsed** | **0.792 s** | < 3.0 s | **PASS** |
| **Disk Drain Throughput** | **63,141.1 records/s** | > 20,000 records/s | **PASS** |
| **Events Written to Disk** | **50,000** | 50,000 | **100.00% Written** |
| **Dropped Records** | **0** | 0 | Zero Data Loss |
| **I/O Write Errors** | **0** | 0 | Clean Disk I/O |
| **Export File Size** | **9.67 MB** | — | Uncompressed JSONL |
| **Avg Bytes per Record** | **202.7 bytes** | < 256 bytes | Compact Serialization |
| **Net RSS Growth** | **+4.88 MB** | < 15.0 MB | Bounded |

---

## 4. Bounded Queue Saturation & Drop Accounting Audit

To stress-test queue overflow behavior and verify Rule 3 ("Never silently discard bad data — record and expose drop/eviction counters"), a burst of 10,000 events was directed at a queue configured with a capacity of 2,000 slots.

| Metric | Measured Value | Target / Expectation | Audit Verdict |
|:---|:---:|:---:|:---:|
| **Queue Capacity** | 2,000 slots | Configured Limit | Verified |
| **Instantaneous Burst Count** | 10,000 records | 5x Over-capacity | Injected |
| **Events Written to File** | 2,000 records | 2,000 | **100.0% Expected** |
| **Dropped Records Counter** | 8,000 records | 8,000 | **100.0% Expected** |
| **Sum Accounted** | **10,000 records** | **10,000** | **Exact Equality** |
| **Silent Drops** | **0** | **0** | **ZERO SILENT DROPS** |
| **Deadlock / Freeze** | **None** | None | Graceful Return |

---

## 5. Sequence Domain Separation Verification

Following the resolution of the earlier sequence gap issue, sequence tracking was verified across both domains:
1. **Transport Stream Domain (`StreamHealth`)**: Tracks daemon-global broadcast sequence (`seq`). Zero sequence gaps observed across all 100,000 events.
2. **Upstream Feed Domain (`VenueHealth`)**: Tracks independent per-venue feed sequences (`feed_seq`). Each venue adapter tracks its own upstream counter independently without false gap cross-contamination.
