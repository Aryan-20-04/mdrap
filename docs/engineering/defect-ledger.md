# MDRAP Engineering Defect Ledger

**Milestone:** v3.1.1 Production-Readiness Remediation  
**Baseline Git Commit:** `4c5a0d8141f60a4fcf82ca3ed3885e679563e9ba` (HEAD of `main`)  
**Audit Baseline Date:** 2026-10-15  
**Review Policy:** All defects must be reproduced against the current checkout and tracked until verified fixed in a specific phase gate.

---

## Defect Inventory & Current Reproduction Status

| ID | Title / Subsystem | Severity | Status on Checkout | Target Phase | Verified Fix Commit |
|---|---|---|---|---|---|
| **F-01** | Simulated Consensus Dual-Leader Split-Brain (`consensus.py`) | P0 (Critical) | **REPRODUCED** | Phase 1 (De-market/Scope) | Pending Phase 1 |
| **F-02** | In-Process Timer Labeled as "Failover Benchmark" | P1 (High) | **REPRODUCED** | Phase 1 & 7 | Pending Phase 1/7 |
| **F-03** | Mismatched Failover Percentiles vs Raw Samples | P1 (High) | **REPRODUCED** | Phase 1 & 7 | Pending Phase 1/7 |
| **F-04** | WAL Batch Append Non-Atomic Across Rotations (`ingestlog.py`) | P0 (Critical) | **REPRODUCED** | Phase 3 | Pending Phase 3 |
| **F-05** | Undefined `IngestLogPoisonedError` Raises `NameError` | P1 (High) | **REPRODUCED** | Phase 3 | Pending Phase 3 |
| **F-06** | Claimed "CRC32C" is IEEE CRC-32 & Omits Headers | P1 (High) | **REPRODUCED** | Phase 1 & 3 | Pending Phase 1/3 |
| **F-07** | Linux POSIX SHM Unlink Prevents Restart Detection (`shm.py`) | P0 (Critical) | **REPRODUCED** (POSIX model) | Phase 2 | Pending Phase 2 |
| **F-08** | Ingestion Loop Dies on Malformed Input (`service.py`) | P0 (Critical) | **REPRODUCED** | Phase 4 | Pending Phase 4 |
| **F-09** | Missing/Unknown Event Types Accepted as Valid (`engine.py`) | P0 (Critical) | **REPRODUCED** | Phase 5 | Pending Phase 5 |
| **F-10** | Native C vs Python Quality Decision Divergence (`fastpath.py`) | P1 (High) | **REPRODUCED** | Phase 5 | Pending Phase 5 |
| **F-11** | Fan-Out Benchmark Delivery Rate > 100% Contaminated | P1 (High) | **REPRODUCED** | Phase 7 | Pending Phase 7 |
| **F-12** | Native Parity Tests Skipped When C Lib Absent | P2 (Medium) | **CONFIRMED** | Phase 5 & 10 | Pending Phase 5/10 |
| **F-13** | Core Scope Sprawl (Options, TCA, Risk, Marine AIS) | P2 (Medium) | **CONFIRMED** | Phase 8 | Pending Phase 8 |

---

## Defect Details & Evidence

### F-01: In-Process Consensus Coordinator Dual-Leader Split-Brain
- **File:** `src/mdrap/consensus.py:108` (`ConsensusCoordinator`, `FencedWALWriter`)
- **Severity:** P0 (Critical)
- **Reproduction Command:**
  ```python
  from mdrap.consensus import ConsensusCoordinator, FencedWALWriter
  c1 = ConsensusCoordinator("node_1", ["node_1", "node_2", "node_3"])
  c2 = ConsensusCoordinator("node_2", ["node_1", "node_2", "node_3"])
  t1 = c1.request_leadership()
  t2 = c2.request_leadership()
  writer = FencedWALWriter("partition-1")
  writer.validate_write(t1)  # Accepted
  writer.validate_write(t2)  # Also accepted (epoch=1 for both!)
  ```
- **Expected Result:** Multi-node consensus requires network quorum or distributed lease (e.g. Raft/etcd/Chubby). A split-brain scenario must reject writes from uncoordinated nodes.
- **Actual Result:** `ConsensusCoordinator` maintains strictly local in-process state (`self._isolated_peers = set()`). Two independent processes or coordinators both acquire leadership at `epoch=1` simultaneously, and `FencedWALWriter` accepts writes from both.
- **Root Cause:** In-process mock object presented as distributed cluster consensus.
- **Resolution Strategy:** Formally de-market distributed consensus in Phase 1; scope MDRAP as a single-node engine; document `consensus.py` as an in-process local simulation or remove from core API.

---

### F-02 & F-03: In-Process Timer Benchmarks & Mismatched Published Percentiles
- **Files:** `benchmarks/failover_benchmark.py`, `audit/phase10/failover_benchmark_results.json`
- **Severity:** P1 (High)
- **Reproduction Command:** Inspect `benchmarks/failover_benchmark.py:43` and `audit/phase10/failover_raw_samples.json`.
- **Expected Result:** Failover benchmarks represent independent host termination, network heartbeats, and re-election. Published statistics must be reproducible from raw sample outputs.
- **Actual Result:** `failover_benchmark.py` directly mutates `coord_s._current_epoch = coord_p._current_epoch` on the same process; published latency (~105 ms) is dominated by an artificial `time.sleep(0.1)` (100 ms) call.
- **Resolution Strategy:** Withdraw multi-host failover benchmarks in Phase 1; replace with single-node crash-restart and WAL salvage benchmarks in Phase 7.

---

### F-04: WAL Batch Append Non-Atomic Across Rotations
- **File:** `src/mdrap/ingestlog.py:446` (`IngestLog.append_batch`)
- **Severity:** P0 (Critical)
- **Status:** FIXED (Phase 3)
- **Reproduction Command:**
  ```python
  from mdrap.ingestlog import IngestLog
  from mdrap.models import RawEvent
  log = IngestLog(tmpdir)
  e1 = RawEvent(source="A", receive_timestamp=1.0, payload={"i": "X", "val": 1})
  e2 = RawEvent(source="A", receive_timestamp=1.0, payload={"i": "X", "val": object()}) # Unserializable
  try:
      log.append_batch([e1, e2])
  except TypeError:
      pass
  # e1 was persisted to disk and offset advanced to 1, despite batch failure!
  ```
- **Expected Result:** A failed batch is completely rolled back; no unacknowledged records are exposed on reopen; retry of the batch does not duplicate events.
- **Actual Result:** Earlier implementation iterated over events record-by-record and rotated segments midway without transactional atomicity.
- **Resolution:** Re-architected `append_batch()` into three transactional stages: in-memory preparation and pre-validation (serialization errors abort before any disk I/O), pre-rotation (if total batch bytes exceed segment capacity, rotate before writing any frames), and single contiguous `write()` with exact rollback truncation on I/O error. Verified with `test_f04_unserializable_event_in_batch_aborts_without_orphan_frames` and `test_f04_io_failure_during_batch_rolls_back_cleanly_without_orphans`.

---

### F-05: Undefined `IngestLogPoisonedError`
- **File:** `src/mdrap/ingestlog.py:458`
- **Severity:** P1 (High)
- **Status:** FIXED (Phase 3)
- **Reproduction Command:**
  ```python
  from mdrap.ingestlog import IngestLog
  log = IngestLog(tmpdir)
  log._is_poisoned = True
  log.append_batch([{"a": 1}])
  # Raises: NameError: name 'IngestLogPoisonedError' is not defined
  ```
- **Expected Result:** Raises `IngestLogPoisonedError(IngestLogError)`.
- **Actual Result:** Raised `NameError: name 'IngestLogPoisonedError' is not defined`.
- **Resolution:** Defined `class IngestLogPoisonedError(IngestLogError): pass` in `src/mdrap/ingestlog.py` and exported it via `src/mdrap/__init__.py`. Updated `append()` and `append_batch()` to raise `IngestLogPoisonedError` when poisoned. Verified with `test_f05_poisoned_error_defined`.

---

### F-06: IEEE CRC-32 Claimed as "CRC32C" & Omits Frame Headers
- **File:** `src/mdrap/ingestlog.py:385, 389`
- **Severity:** P1 (High)
- **Status:** FIXED (Phase 1 & Phase 3)
- **Reproduction Command:** Inspect `crc = zlib.crc32(payload_bytes) & 0xFFFFFFFF`.
- **Expected Result:** Checksum implementation matches documentation and covers data payload integrity.
- **Actual Result:** Docs claimed Castagnoli CRC32C while code uses standard IEEE 802.3 CRC-32 (`zlib.crc32`).
- **Resolution:** Corrected all documentation across `README.md`, `status.md`, and module docs to accurately specify IEEE 802.3 CRC-32, preserving binary disk layout compatibility. Verified with `test_f06_wal_verify_clean_and_corrupt` and `IngestLog.verify()`.

---

### F-07: Linux POSIX Shared-Memory Reader Misses Publisher Restart
- **File:** `src/mdrap/shm.py:641` (`SHMReader.check_epoch_valid`)
- **Severity:** P0 (Critical)
- **Status:** FIXED (Phase 2)
- **Reproduction Status:** On POSIX/Linux, unlinking `/dev/shm/<name>` keeps existing file descriptors alive on the old unlinked inode. Readers reading `offset 16` of `self.shm.buf` read the old unlinked inode and report `check_epoch_valid() == True`, failing to detect the new publisher.
- **Resolution:** Implemented `ReaderState` FSM (`DETACHED -> ATTACHED -> INVALIDATED -> REATTACHING -> ATTACHED`), POSIX `st_nlink == 0` unlinked inode detection via open fd `fstat`, heartbeat staleness check, active named segment epoch probe, and safe multiprocessing `resource_tracker` unregistration without `KeyError`. Verified across 25 consecutive restart test runs (25/25 PASS).

---

### F-08: Malformed Input Crashes Background Ingestion Loop
- **File:** `src/mdrap/service.py:655, 683` (`_ingestion_loop`, `_process_and_broadcast`)
- **Severity:** P0 (Critical)
- **Status:** FIXED (Phase 4)
- **Reproduction Command:** Submit raw event where `exchange_ts="INVALID_STRING"`. `float("INVALID_STRING")` raises `ValueError`, killing `_ingestion_loop` thread while daemon remains marked `_running = True`.
- **Expected Result:** Malformed event is trapped, quarantined to dead-letter, and error counter incremented; ingestion thread continues processing subsequent valid events.
- **Actual Result:** Worker thread terminated with uncaught exception; daemon reported running while completely stalled.
- **Resolution:** Added safe type-tolerant timestamp and numeric parsing in `_process_and_broadcast()`. Wrapped feed polling loops in `_ingestion_loop()` with exception trapping, logging, and `_ingestion_errors` incrementing. Updated `is_degraded()` to verify `_ingest_thread.is_alive()` and error rate thresholds, exposing worker thread health directly in telemetry and status queries. Verified with `tests/test_phase4_ingest_failure_isolation.py`.

---

### F-09: Missing / Unknown Event Types Accepted as Valid
- **File:** `src/mdrap/engine.py:736, 1001`
- **Severity:** P0 (Critical)
- **Reproduction Command:**
  ```python
  from mdrap.engine import Engine
  from mdrap.models import RawEvent
  engine = Engine.open(None)
  raw = RawEvent(source="X", receive_timestamp=1.0, payload={"instrument": "AAPL", "price": 100.0, "event_type": "UNKNOWN_GARBAGE"})
  dec = engine.submit(raw)
  print(dec.quality_status)  # Outputs: QualityStatus.VALID!
  ```
- **Expected Result:** Unknown or missing event types are rejected with `QualityStatus.INVALID` and `Reason.SCHEMA_VIOLATION`.
- **Actual Result:** Missing `event_type` defaults to `"TRADE"` (line 736); unknown string in `event_type` falls back to `EventType.UNKNOWN` (line 1004) and is published as `QualityStatus.VALID`.
- **Resolution Strategy:** In Phase 5, strictly validate `event_type` against allowed enum values; quarantine missing/unknown types.

---

### F-10 & F-12: Native C vs Python Quality Decision Divergence
- **File:** `src/mdrap/fastpath.py:1076`
- **Severity:** P1 (High)
- **Reproduction Command:** Inspect `1 if event.event_type == EventType.QUOTE else 0` passed to `_C_EXT.eval_fast2`.
- **Expected Result:** Native C fastpath and Python `QualityEngine` handle all event types (`TRADE`, `QUOTE`, `BBO`, `BOOK`, `DEPTH`, `UNKNOWN`) with identical decisions and reason bitmasks.
- **Actual Result:** Native C engine only distinguishes `QUOTE` vs non-quote (treated as `TRADE`), leading to divergent evaluations on order book and depth events.
- **Resolution Strategy:** In Phase 5, align C kernel signatures and event-type handling; run automated differential fuzzing.

---

### F-11: Fan-Out Benchmark Delivery Rate > 100%
- **Files:** `audit/phase10/fanout_client_receipt_results.json`, `audit/phase10/fanout_networked_results.json`
- **Severity:** P1 (High)
- **Reproduction Command:** Inspect recorded JSON:
  `"delivery_rate_percent": 257.65`
- **Expected Result:** Delivery rate cannot exceed 100.0%.
- **Actual Result:** Ticks from background simulator were received by subscriber sockets during benchmark runs, contaminating test counts.
- **Resolution Strategy:** In Phase 7, tag benchmark events with unique sequence IDs; compute delivery rate strictly on tagged test frames.

---

### F-13: Core Scope Sprawl
- **Files:** `src/mdrap/options.py`, `src/mdrap/tca.py`, `src/mdrap/risk.py`, `src/mdrap/vessel.py`
- **Severity:** P2 (Medium)
- **Expected Result:** Core engine focused strictly on high-throughput market data normalization, quality validation, and capture.
- **Actual Result:** Financial analytics (Greeks, VaR, TCA) and unrelated marine tracking expand maintenance and audit surface.
- **Resolution Strategy:** In Phase 8, classify modules; define clean extension interfaces without breaking backward compatibility.
