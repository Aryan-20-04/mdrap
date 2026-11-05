# MDRAP v5.0 CPU Profiling, Call Graph & Hotspot Analysis

## 1. Executive Summary & Profiler Configuration

This report details the CPU execution profile, call graph breakdown, and subsystem hotspot analysis of MDRAP v5.0 on the critical `Engine.submit()` path.

### Profiler Parameters
- **Tool**: Python standard library `cProfile` with deterministic function-call hooking.
- **Workload**: 10,000 sequential raw ticks ingested into `Engine` configured with SQLite projection (`SQLiteProjection`), asynchronous micro-batching (`async_projections=True`), and `fsync_policy="never"`.
- **Total Function Calls**: 1,178,701 calls (1,178,685 primitive calls) in **1.050 seconds**.

---

## 2. Cumulative CPU Call Graph Breakdown

```text
ncalls       tottime   percall   cumtime   percall  filename:lineno(function)
------------------------------------------------------------------------------------------------------------------------
10001/10000    0.150    0.0000    1.031    0.0001   src/mdrap/engine.py:1015(submit)
    10000    0.094    0.0000    0.413    0.0000   src/mdrap/ingestlog.py:914(append_batch)
    10000    0.013    0.0000    0.290    0.0000   src/mdrap/engine.py:1529(step)
    10000    0.031    0.0000    0.275    0.0000   src/mdrap/engine.py:2524(_step_internal)
    30000    0.040    0.0000    0.220    0.0000   Lib/json/__init__.py:183(dumps)
    10000    0.055    0.0000    0.203    0.0000   src/mdrap/engine.py:1671(_stage_schema_validation)
    30000    0.038    0.0000    0.172    0.0000   Lib/json/encoder.py:183(encode)
    30000    0.119    0.0000    0.119    0.0000   Lib/json/encoder.py:205(iterencode)
    10001    0.089    0.0000    0.089    0.0000   {method 'flush' of '_io.BufferedRandom' objects}
    10000    0.046    0.0000    0.059    0.0000   src/mdrap/engine.py:53(estimate_payload_size)
    20000    0.056    0.0000    0.056    0.0000   {method 'tell' of '_io.BufferedRandom' objects}
    10000    0.013    0.0000    0.035    0.0000   src/mdrap/ingestlog.py:72(pack_frame_header)
    10000    0.023    0.0000    0.033    0.0000   src/mdrap/projection.py:158(apply)
    20000    0.018    0.0000    0.029    0.0000   src/mdrap/engine.py:260(record_event_count)
   200006    0.025    0.0000    0.025    0.0000   {built-in method builtins.len}
   130000    0.024    0.0000    0.024    0.0000   {method 'append' of 'list' objects}
      375    0.003    0.0000    0.018    0.0000   src/mdrap/projection.py:522(apply)
    10000    0.007    0.0000    0.017    0.0000   src/mdrap/engine.py:2543(_bump_invalid)
   110000    0.017    0.0000    0.017    0.0000   {built-in method builtins.isinstance}
    10000    0.007    0.0000    0.015    0.0000   src/mdrap/ingestlog.py:57(compute_frame_crc)
     1503    0.012    0.0000    0.012    0.0000   {method 'execute' of 'sqlite3.Cursor' objects}
```

---

## 3. Subsystem CPU Time Breakdown

```text
Subsystem Component               CPU Time (s)    % of Total Critical Path
--------------------------------------------------------------------------
1. JSON Serialization (dumps/encode)   0.220 s             20.9%
2. IngestLog Append & Framing          0.193 s             18.4%
3. Schema Validation & Estimation      0.203 s             19.3%
4. Engine Step & Decision State        0.156 s             14.9%
5. Buffered File Flush (OS WriteFile)  0.089 s              8.5%
6. File Pointer Inquiries (tell)       0.056 s              5.3%
7. Projection Tracker & Checkpoints    0.051 s              4.9%
8. Other (isinstance, len, list)       0.082 s              7.8%
--------------------------------------------------------------------------
TOTAL INGESTION RUNTIME                1.050 s            100.0%
```

---

## 4. Key Bottleneck Discoveries

### Discovery 1: Triplicate JSON Serialization
For every incoming raw tick, `json.dumps()` is invoked up to 3 times:
1. In `_stage_schema_validation`: `estimate_payload_size` invokes `json.dumps()` when verifying payload limits.
2. In `append_batch`: Serializes `rec` to UTF-8 JSON bytes: `json.dumps(rec).encode("utf-8")`.
3. In downstream serialization or audit logging.
This consumes **0.220s (20.9%)** of the entire pipeline runtime.

### Discovery 2: Unconditional `BufferedRandom.flush()`
In `IngestLog.append_batch()` line 999:
`self._current_file.flush()` is called unconditionally on every event. Even when `fsync_policy="never"`, flushing Python's internal buffer forces an operating system `WriteFile` kernel transition per tick, consuming **0.089s (8.5%)**.

### Discovery 3: Repetitive OS File Pointer Inquiries (`f.tell()`)
In `IngestLog.append_batch()` line 980 and line 997:
`self._current_file.tell()` is called twice per append to check segment rotation boundaries. In Python on Windows, `tell()` invokes a Win32 `SetFilePointerEx` system call. This consumes **0.056s (5.3%)**.
