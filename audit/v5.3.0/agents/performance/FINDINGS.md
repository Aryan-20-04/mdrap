# Performance & Latency Engineering — Detailed Findings

## Executive Summary
Audited benchmark infrastructure, hot path micro-optimizations, throughput claims vs raw committed data, memory bounds, SQLite batch operations, and pure Python fallback parity. The compiled C kernel (`fastpath.c`) demonstrates exemplary AVX2/SSE4.2 pre-fetching and fixed-point arithmetic. However, a CRITICAL semantic divergence exists between Python and C on `HALT` and `STATUS` events, and benchmark documentation inflates throughput by 22.7% over raw committed results.

## Categorized Findings

### CRITICAL
1. **F-PERF-01: Semantic Divergence on `HALT` and `STATUS` Events (C Rejecting Valid Events)**:
   - `src/mdrap/quality.py:360-366, 390-392`: Pure Python marks `HALT` and `STATUS` events as `QualityStatus.VALID`.
   - `src/mdrap/fastpath.py:1232-1240`: Maps `HALT`/`STATUS` to `c_ev_type = -1`.
   - `src/mdrap/fastpath.c:677-680`: Unconditionally marks `ev->event_type != 0 && ev->event_type != 1` as `STATUS_INVALID` with `REASON_SCHEMA_VIOLATION`.
   - **Impact**: Pure Python marks trading halts as `VALID`; Native C fastpath marks them as `INVALID`. Violates GEMINI.md Rule 4 (Pure Python Fallback Parity).

### HIGH
2. **F-PERF-02: Published Benchmarks Inflate Committed Raw Data by 22.7%**:
   - `README.md` and `docs/benchmarks.md` publish:
     - Native C Fastpath: **510,820 EPS** (p50: 1.60 µs)
     - Pure Python Fallback: **170,080 EPS** (p50: 8.30 µs)
     - Speedup: **3.00x**
   - Actual committed data in `audit/v5.3/raw/benchmarks_raw.json`:
     - Native C Fastpath: **416,331.2 EPS** (p50: 1.70 µs)
     - Pure Python Fallback: **149,885.7 EPS** (p50: 4.40 µs)
     - True speedup: **2.78x**
   - Published numbers are inflated by **+22.7%** (C) and **+13.5%** (Python).

3. **F-PERF-03: Invalid Reproduction Command in README**:
   - `README.md:156` cites `python benchmarks/bench_scale.py` to reproduce 43.67M EPS Native C kernel.
   - `bench_scale.py` runs Python `Engine.submit()` at 20k–50k EPS; does not test isolated C kernel.

4. **F-PERF-04: Redundant `json.dumps()` on Every Single Ingestion Tick**:
   - `src/mdrap/pipeline.py:787-791`: `isinstance(event.raw_id, int)` evaluates to `False` on 100% of ticks because `raw_id` is a string (`raw-RUNID-N`).
   - Executes `json.dumps([event.raw_id])` for every processed event on the hot path.

5. **F-PERF-05: Missing Backpressure Stall Telemetry in Pipeline**:
   - `src/mdrap/pipeline.py:1163-1175`: Catches `queue.Full` on saturated write queue, but never invokes `self.metrics.record_streaming(..., backpressure_stall=True)`. Telemetry reports 0 stalls while dropping batches.

### MEDIUM
6. **F-PERF-06: Unbounded Dictionary Growth in `FastQualityEngine`**:
   - `src/mdrap/fastpath.py:1101-1104`: `self._inst_map` stores instrument strings indefinitely without eviction, unlike `QualityEngine` which bounds tracked instruments at 100,000.

7. **F-PERF-07: Synthetic Loop Reported as Ingress Benchmark**:
   - `benchmarks/run_v53_unified_benchmark.py:72-77`: Boundary A measures Python list comprehension traversal of pre-allocated list, claiming 16.1M EPS "ingress wire parsing".

### LOW / INFO
8. **F-PERF-08: Disk Durability and Batch Operations Verified**:
   - `src/mdrap/storage.py` is 100% compliant with `executemany`, WAL mode, 256MB mmap, and atomic batch transactions.\n