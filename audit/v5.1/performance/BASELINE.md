# MDRAP v5.1 — Performance Baseline Record

**Document**: `audit/v5.1/performance/BASELINE.md`  
**Host Environment**: 12th Gen Intel Core i7-1255U (10 Cores / 12 Threads), 15.7 GB RAM, NVMe SSD, Windows 11 AMD64  
**Runtime**: Python 3.13.1, MinGW-w64 GCC 14.2.0  
**Baseline Hash**: `6bdd3ea`  
**Date**: 2026-11-05

---

## 1. Verified Raw Performance Metrics

All v5.0 benchmark numbers recorded in `audit/v5/performance/RAW_BENCHMARK_RESULTS/benchmark_12_tiers_raw.json` serve as the frozen comparison baseline for all v5.1 architectural experiments.

```json
{
  "timestamp": 1793862854.07,
  "system": {
    "cpu_logical": 12,
    "cpu_physical": 10,
    "ram_gb": 15.72,
    "start_rss_mb": 61.38,
    "end_rss_mb": 61.38
  },
  "results": [
    {"name": "Native CRC32C", "throughput_gb_sec": 0.405, "eps": 425266.9, "p50_us": 1.9, "p99_us": 6.8},
    {"name": "Native Processing (Batch)", "eps": 271182.2, "p50_us": 3.48, "p99_us": 6.57},
    {"name": "Python/C FFI Crossing", "eps": 312817.1, "p50_us": 2.6, "p99_us": 5.8},
    {"name": "ITCH 5.0 Wire Parser", "eps": 250539.3, "p50_us": 3.4, "p99_us": 7.9},
    {"name": "SBE Wire Decoding", "eps": 303683.6, "p50_us": 2.8, "p99_us": 3.9},
    {"name": "SPSC Lock-Free Queue", "eps": 1235403.7, "p50_us": 0.2, "p99_us": 0.4},
    {"name": "In-Memory Engine Submit", "eps": 27168.9, "p50_us": 32.9, "p99_us": 114.3},
    {"name": "WAL Append (Buffered)", "eps": 63275.6, "p50_us": 13.4, "p99_us": 66.0},
    {"name": "WAL Durability Barrier", "eps": 261.6, "p50_us": 3260.3, "p99_us": 8065.0},
    {"name": "SQLite Projection (Async)", "eps": 78840.2, "p50_us": 1.9, "p99_us": 3.0},
    {"name": "SHM v3 IPC Delivery", "eps": 172636.9, "p50_us": 5.1, "p99_us": 9.6},
    {"name": "Full End-to-End Pipeline", "eps": 14000.6, "p50_us": 58.8, "p99_us": 198.3}
  ]
}
```

---

## 2. v5.1 Evaluation Thresholds

Any candidate change for v5.1 must:
1. Preserve 100% of functional and differential parity tests (`pytest tests/ -v`).
2. Demonstrate measurable reduction in p50/p99 latency or measurable increase in sustainable throughput.
3. Introduce zero unbounded heap allocations in the per-event hot path.
4. Maintain explicit distinction between physical fsync and buffered writes.
