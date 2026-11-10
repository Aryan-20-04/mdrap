# Performance & Latency Engineering — Recommendations

## Priority 0 (Release Blockers)
1. **Fix Fallback Parity for `HALT` and `STATUS` Events (F-PERF-01)**:
   - In `src/mdrap/fastpath.c:677`, accept `ev->event_type == 2` (HALT) and `ev->event_type == 3` (STATUS).
   - In `src/mdrap/fastpath.py:1232`, map `EventType.HALT` to 2 and `EventType.STATUS` to 3.
   - Add test vectors in `tests/test_golden_parity.py` asserting identical `VALID` verdicts for HALT/STATUS across Python and C.
2. **Align Benchmark Documentation with Committed Raw Data (F-PERF-02, F-PERF-03)**:
   - Update `README.md` and `docs/benchmarks.md` to reflect 416.3k EPS Native C, 149.9k EPS Pure Python, 2.78x speedup.
   - Fix reproduction command for the isolated C kernel in `README.md`.

## Priority 1 (Hot Path Optimizations)
3. **Eliminate Hot Path `json.dumps()` in Pipeline (F-PERF-04)**:
   - Pre-format string raw IDs via `f'["{event.raw_id}"]'` or store raw IDs as monotonic integers.
4. **Record Backpressure Telemetry (F-PERF-05)**:
   - Call `self.metrics.record_streaming(..., backpressure_stall=True)` upon queue full timeout.

## Priority 2 (Memory Bounds)
5. **Bound `_inst_map` in `FastQualityEngine` (F-PERF-06)**:
   - Apply LRU capacity limit matching `QualityEngine.max_tracked_instruments` (100,000).\n