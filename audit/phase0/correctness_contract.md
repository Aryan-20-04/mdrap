# MDRAP Phase 0: Correctness Invariants & System Contract

**Document ID**: `MDRAP-AUDIT-P0-INV-002`  
**Phase**: Phase 0 (Baseline Audit & Invariant Specification)  
**Date**: 2026-10-09  
**Git Commit**: `b891898`  
**Status**: Formalized Contract & Invariant Inventory  

---

## 1. Executive Summary

This document formalizes the operational and mathematical contracts of MDRAP. For each area, it defines the current implementation behavior, the desired target contract, relevant source anchors, test coverage, missing verifications, deterministic validation procedures, and violation severity.

---

## 2. Invariant Specifications

### 2.1 Event Identity & Sequencing

#### Invariant `INV-SEQ-001`: Process-Restart Monotonic Event ID Uniqueness
* **Current Behavior**: Monotonic IDs use `itertools.count(1)` combined with an environment or random 64-bit run identifier `_RUN_ID = secrets.token_hex(8)` (`evt-{RUN_ID}-{N}`).
* **Desired Contract**: Monotonic IDs must never collide across process restarts, multi-process workers, or replays under identical timestamps.
* **Source Locations**: `src/mdrap/gateway.py:35-42`, `src/mdrap/models.py:75-88`.
* **Existing Tests**: `tests/test_canonical_models.py`, `tests/test_phase1_card1_restart_loss.py`.
* **Missing Tests**: Multi-process concurrent cluster generator ID collision test.
* **Verification Method**: Instantiate 10 concurrent processes generating 100k events each and assert zero primary key collisions in storage.
* **Severity If Violated**: **CRITICAL** (Database primary key collision causing dropped events or transaction rollbacks).

#### Invariant `INV-SEQ-002`: Dual-Sequence Monotonicity (Source vs Internal)
* **Current Behavior**: Preserves external vendor `sequence_number` (may contain gaps or venue resets) while stamping strictly monotonic internal sequence IDs on the SHM ring buffer (`head_seq`).
* **Desired Contract**: Internal pipeline sequence must be strictly continuous ($S_{i+1} = S_i + 1$) per channel, while external exchange sequence gaps are flagged with `Reason.SEQUENCE_GAP`.
* **Source Locations**: `src/mdrap/gateway.py:85-115`, `src/mdrap/shm.py:120-145`, `src/mdrap/quality.py:180-210`.
* **Existing Tests**: `tests/test_reconciliation_invariants.py`, `tests/test_shm_watermark.py`.
* **Missing Tests**: Replay stream where external sequence resets to 0 at midnight EDT.
* **Verification Method**: Inject an external sequence jump from 500 to 1,000; assert `Reason.SEQUENCE_GAP` is tagged and internal SHM sequence advances monotonically by 1.
* **Severity If Violated**: **HIGH** (Downstream trading algorithms cannot detect packet loss or receive corrupt ordering).

#### Invariant `INV-SEQ-003`: 64-Bit Sliding Window Deduplication
* **Current Behavior**: Unsequenced events use a two-generation LRU cache; sequenced events use a 64-bit sliding window bitmap (`fastpath.c`).
* **Desired Contract**: Any event with identical `(source, instrument, sequence)` or identical `(source, instrument, exchange_ts, price, size)` arriving within the dedup horizon must be tagged `DUPLICATE` and quarantined.
* **Source Locations**: `src/mdrap/quality.py:380-450`, `src/mdrap/fastpath.c:165-210`.
* **Existing Tests**: `tests/test_quality.py`, `tests/test_parity_differential.py`.
* **Missing Tests**: Massive burst of 1,000 identical duplicates arriving simultaneously across multiple threads.
* **Verification Method**: Feed 10,000 identical ticks; verify exactly 1 is `VALID` and 9,999 are tagged `DUPLICATE` with 0 unhandled exceptions.
* **Severity If Violated**: **CRITICAL** (Volume inflation, phantom fills in downstream execution models).

---

### 2.2 Durability & Recovery

#### Invariant `INV-DUR-001`: IngestLog WAL Durability Boundary
* **Current Behavior**: `IngestLog` appends 128-byte binary frames to preallocated segment files with CRC-32 header verification and directory fsync.
* **Desired Contract**: An event is considered **durably accepted** only once written to the `IngestLog` OS page cache (or fsynced depending on `sync_on_write` configuration). In-memory queues are explicitly acknowledged as transient.
* **Source Locations**: `src/mdrap/ingestlog.py:85-195`.
* **Existing Tests**: `tests/test_ingestlog.py`, `tests/test_gate_g2_kill9.py`.
* **Missing Tests**: Power-outage simulation test using simulated truncated write at segment boundary.
* **Verification Method**: Execute 25 randomized `kill -9` process crashes mid-stream; assert zero acknowledged event loss upon recovery.
* **Severity If Violated**: **CRITICAL** (Unrecoverable trade data loss during sudden hardware or process failure).

#### Invariant `INV-DUR-002`: Segment Header CRC-32 Tamper & Corruption Detection
* **Current Behavior**: Every 64-byte `IngestLog` segment header embeds a CRC-32 checksum (`struct.unpack('<I', hdr[60:64])`).
* **Desired Contract**: If a single bit in the segment header or metadata is flipped, `IngestLog.replay()` must refuse to load corrupt data and raise `WALCorruptionError`.
* **Source Locations**: `src/mdrap/ingestlog.py:120-165`.
* **Existing Tests**: `tests/test_audit_remediation.py::test_ingestlog_segment_header_crc_and_tamper_detection`.
* **Missing Tests**: Corrupted frame body checksumming (currently CRC covers the segment header).
* **Verification Method**: Flip byte 12 in a WAL file on disk; verify replay aborts safely with forensic audit log entry.
* **Severity If Violated**: **HIGH** (Silent database corruption during disaster recovery).

#### Invariant `INV-DUR-003`: Deterministic Replay Idempotency
* **Current Behavior**: Replaying an `IngestLog` stream produces an identical sequence of `CanonicalEvent` instances with bit-identical quality flags and timestamps.
* **Desired Contract**: Given an identical input WAL log, re-execution through the engine must result in identical state, identical BBO quotes, and zero duplicate rows in storage.
* **Source Locations**: `src/mdrap/replay.py:45-120`, `src/mdrap/storage.py:280-320`.
* **Existing Tests**: `tests/test_replay_idempotency.py`, `tests/test_audit_remediation.py::test_n1_live_equals_replay_property`.
* **Missing Tests**: Replay across process restarts where time-of-day clock differs from recorded event timestamps.
* **Verification Method**: Run 100k events live, record WAL; re-run WAL into fresh database; execute SHA-256 diff on both SQLite databases.
* **Severity If Violated**: **HIGH** (Backtest and audit divergence from live trading).

---

### 2.3 Data Quality & Quarantine

#### Invariant `INV-QUAL-001`: Strict Quality Precedence
* **Current Behavior**: Quality status assignment follows strict non-downgrading precedence:
  $$\text{INVALID} > \text{SUSPICIOUS} > \text{VALID}$$
* **Desired Contract**: Once an event receives an `INVALID` classification (e.g. `SCHEMA_VIOLATION` or `NEGATIVE_PRICE`), subsequent rule checks may append reasons but cannot downgrade the status to `SUSPICIOUS` or `VALID`.
* **Source Locations**: `src/mdrap/quality.py:95-135`, `src/mdrap/fastpath.c:85-115`.
* **Existing Tests**: `tests/test_quality.py`, `tests/test_parity_differential.py`.
* **Missing Tests**: Multi-rule collision matrix fuzzing.
* **Verification Method**: Inject an event with both a 6-sigma outlier (SUSPICIOUS) and a negative price (INVALID); verify status is `INVALID`.
* **Severity If Violated**: **CRITICAL** (Invalid market data leaking into live trading strategies).

#### Invariant `INV-QUAL-002`: Quarantine-Never-Drop Principle
* **Current Behavior**: Events failing schema normalization or marked `INVALID` are persisted to the `quarantine` database table with the full raw payload intact.
* **Desired Contract**: Under no circumstance may bad, malformed, or unparseable input be silently discarded without an audit log entry and quarantine persistence.
* **Source Locations**: `src/mdrap/pipeline.py:180-240`, `src/mdrap/storage.py:340-390`.
* **Existing Tests**: `tests/test_quarantine_subsystem.py`, `tests/test_error_surfacing.py`.
* **Missing Tests**: Quarantine table insertion under full disk condition.
* **Verification Method**: Stream 5,000 malformed JSON frames; assert quarantine table count increases by exactly 5,000 and dropped counter equals 0.
* **Severity If Violated**: **CRITICAL** (Regulatory violation under SEC Rule 613 / CAT reporting).

#### Invariant `INV-QUAL-003`: Dynamic 6-Sigma Price Anomaly Detection & Reseeding
* **Current Behavior**: Evaluates price deviation against Welford running variance with a 2 bps relative floor. Reseeds automatically after 8 consecutive consistent outliers to adapt to genuine market regime shifts.
* **Desired Contract**: Sudden price spikes (>6 standard deviations) are quarantined as `SUSPICIOUS`, while genuine market regime changes (e.g. Fed rate announcement) resume normal processing after the reseed threshold.
* **Source Locations**: `src/mdrap/quality.py:280-360`, `src/mdrap/fastpath.c:260-310`.
* **Existing Tests**: `tests/test_quality.py`, `tests/test_fastpath_quantitative.py`.
* **Missing Tests**: Reseeding behavior when price drops to 0.0001 (penny stock / crypto liquidation).
* **Verification Method**: Simulate a sudden 15% price step jump; assert ticks 1–7 are `SUSPICIOUS`, tick 8 reseeds, tick 9+ are `VALID`.
* **Severity If Violated**: **MEDIUM** (False positive market halt or failure to catch fat-finger orders).

---

### 2.4 Concurrency & IPC

#### Invariant `INV-IPC-001`: Two-Phase Seqlock Commit Protocol
* **Current Behavior**: The producer writes the slot payload completely, issues a hardware memory barrier, and then atomically increments the sequence number. Readers verify that `seq_after == seq_before` and `seq` is even.
* **Desired Contract**: Readers must never observe partial writes, torn values, or uncommitted slot state, even under maximum thread preemption.
* **Source Locations**: `src/mdrap/shm.py:85-170`, `src/mdrap/mdrap_core.c:110-165`.
* **Existing Tests**: `tests/test_shm.py`, `tests/test_shm_fuzz.py`.
* **Missing Tests**: Weak memory-ordering architectures (ARM64 Apple Silicon / AWS Graviton) cross-core test.
* **Verification Method**: 1 producer writing 5,000,000 ticks against 4 concurrent reader processes; assert zero torn float values or non-monotonic timestamps.
* **Severity If Violated**: **CRITICAL** (Torn prices causing catastrophic trading execution errors).

#### Invariant `INV-IPC-002`: Overrun & Lap Detection for Slow Consumers
* **Current Behavior**: If reader sequence falls behind the producer by more than `slot_count` (16,384 slots), reader detects lap, logs overrun telemetry, and safely skips to the current head sequence.
* **Desired Contract**: A slow consumer must never block the hot-path writer or cause process starvation, and must reliably detect dropped slots upon falling behind.
* **Source Locations**: `src/mdrap/shm.py:210-265`.
* **Existing Tests**: `tests/test_shm_decoupled.py`, `tests/test_shm_drainer.py`.
* **Missing Tests**: Sustained multi-minute overrun loop stress.
* **Verification Method**: Pause reader process for 200 ms while writer produces 50k ticks; resume reader and verify overrun counter equals 1 and sequence advances to head.
* **Severity If Violated**: **HIGH** (Slow analytical process degrading high-frequency market-making thread).

---

### 2.5 Operational Behavior

#### Invariant `INV-OPS-001`: Watchdog Silence & Automated Failover
* **Current Behavior**: `SourceWatchdog` monitors per-source arrival intervals with an adaptive multiplier (10x EWMA tick interval, minimum 2.0s). Triggers automated failover alert upon silence.
* **Desired Contract**: If the primary source stops broadcasting, the watchdog must transition source state from `HEALTHY` to `SILENT` within the timeout window and route canonical BBO to the next highest-reliability secondary feed.
* **Source Locations**: `src/mdrap/watchdog.py:75-180`, `src/mdrap/failover.py:45-95`.
* **Existing Tests**: `tests/test_watchdog.py`, `tests/test_failover_and_dr.py`.
* **Missing Tests**: Feed rapidly flapping between healthy and silent every 100 ms.
* **Verification Method**: Silence FEEDX mid-stream; assert watchdog alert is logged in < 0.1s and FEEDY is selected as canonical source.
* **Severity If Violated**: **HIGH** (Trading strategy operates on frozen/stale market quotes).

#### Invariant `INV-OPS-002`: Granular Role-Based Access Control (RBAC) & Entitlements
* **Current Behavior**: Every API endpoint and streaming topic enforces `Role` (`VIEWER`, `OPERATOR`, `ADMIN`) and checks symbol/venue entitlement bitmasks.
* **Desired Contract**: Unauthenticated or unauthorized callers must be rejected with HTTP 401/403 and immediately disconnected from WebSocket feeds without leaking internal state.
* **Source Locations**: `src/mdrap/security.py:480-620`, `src/mdrap/api.py:120-210`.
* **Existing Tests**: `tests/test_api_auth.py`, `tests/test_entitlements.py`.
* **Missing Tests**: Timing attack resistance on HMAC token validation.
* **Verification Method**: Issue REST queries with invalid token, valid token with VIEWER role against ADMIN endpoint; assert all return 401/403 with audit logs.
* **Severity If Violated**: **HIGH** (Unauthorized trading, audit non-compliance).
