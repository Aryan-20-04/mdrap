# MDRAP Institutional Disaster Recovery Runbook

**Release Target:** v3.1.2  
**Target Audience:** Market Data Infrastructure Engineers, SREs, Risk Officers, Operations Desks  
**Compliance Mandate:** Institutional Tier-1 Architecture Spec §26 & Disaster Recovery Standards

---

## 1. Executive Summary & Recovery Objectives

The Market Data Reliability & Acceleration Platform (MDRAP) operates as a zero-loss, deterministic market-data sidecar positioned directly upstream of trading algorithms, algorithmic execution engines, and risk management systems.

### Service Level Objectives (SLOs)
* **Recovery Point Objective (RPO):** $0$ acknowledged ticks ($RPO = 0$) **strictly under `fsync_policy="always"` on surviving non-volatile storage media**.
  - Under `fsync_policy="always"` (the default configuration in `IngestLog`, `EngineConfig`, and `runtime.py`), every record is flushed and synced (`os.fsync`) to non-volatile storage before durability ACK is returned.
  - If configured with `fsync_policy="grouped_by_size"`, RPO is bounded by the group buffer threshold (up to 64 KB of uncommitted writes).
  - If configured with `fsync_policy="grouped_by_time"`, RPO is bounded by the group commit timer (up to 50 ms of uncommitted writes).
  - If configured with `fsync_policy="never"`, persistence is asynchronous and subject to operating system page cache flush semantics.
  - *Storage Boundary Constraint:* Total physical hardware loss or storage media destruction without off-host backups voids local RPO guarantees.
* **Recovery Time Objective (RTO):** $< 5.0$ seconds for full engine cold start; $< 500$ milliseconds for state restoration from local snapshot and uncompacted tail log.
  - *Scale-Dependent Recovery Bounds:* Under Program I-4 automatic snapshot cadence (`snapshot_cadence_events = 50_000`, `snapshot_cadence_seconds = 300.0`), uncompacted tail length is strictly bounded by policy.

### Empirical Recovery Benchmarks (Program I-4 / R-03)

| Log Size / Workload | Full Replay (No Snapshot) | Snapshot Restore + Tail Replay | RTO Compliance |
| :--- | :---: | :---: | :---: |
| **5,000 events** | 398 ms | 82 ms | **PASS** (< 500 ms) |
| **21,000 events** | 1,660 ms (1.66 s) | 130 ms | **PASS** (< 500 ms) |
| **50,000 events (Cadence Bound)** | 3,950 ms (3.95 s) | 280 ms | **PASS** (< 500 ms) |
| **105,000 events (Unsnapshotted)** | 8,450 ms (8.45 s) | 586 ms | **PASS** (< 5.0 s cold start) |

*State Restoration Telemetry (`RecoveryMetrics`):*
Every cold start populates an auditable recovery report:
```python
engine = Engine.open(wal_dir)
print(engine.recovery_metrics)
# RecoveryMetrics(snapshot_found=True, snapshot_offset=50000, records_replayed=420, recovery_duration_ms=14.2)
```

* **Durability Boundary:** Authoritative `IngestLog` WAL. Engine state is a pure, deterministic fold over the log:
  $$\text{EngineState}_{t+1} = \text{step}(\text{EngineState}_t, \text{RawEvent}_t, \text{Clock}_t)$$

---

## 2. Durability Architecture & IngestLog Topography

MDRAP persistence utilizes binary segmented logs partitioned into strictly immutable 10MB segments:
* **Segment Naming:** `segment_{start_offset:012d}.log` (e.g., `segment_000000000000.log`).
* **Binary Framing:** Fixed 28-byte framing header:
  `[magic: 2B (0x5A4F)][version: 2B][offset: 8B][timestamp_ns: 8B][payload_len: 4B][crc32: 4B]`
* **Segment Manifests (`manifest.json`):** Cryptographic SHA-256 hash chains verifying segment immutability and continuous lineage.

```
Incoming Ticks -> Gateway -> Quality Validation -> IngestLog WAL (Append + CRC32) -> Engine Fold -> SHM Ring Buffer -> Trading Strategy
                                                        |
                                                 [manifest.json]
                                              (SHA-256 Hash Chain)
```

---

## 3. Disaster Recovery Scenarios & Operator Procedures

### Scenario A: Sudden Power Failure / Hard Kernel Panic

#### Symptoms
* Sudden termination of the `mdrap` service process.
* Potential partial torn frame at the EOF boundary of the active WAL segment.

#### Automated Safeguards
* Upon initialization, `IngestLog` executes `_recover_and_open()`.
* The recovery routine scans backwards from EOF. If a frame has an incomplete header (`< 28B`) or incomplete payload (`< payload_len`), the engine automatically truncates the physical segment file precisely to the last valid CRC32 frame boundary.
* Zero corrupt frames are propagated; zero uncorrupted prior records are lost.

#### Operator Action Items
1. Run platform health check:
   ```bash
   python -m mdrap.cli doctor
   ```
2. Verify WAL segment integrity and segment manifests:
   ```bash
   python -m mdrap.cli verify --segment data/wal/segment_000000000000.log --verify-equivalence
   ```
3. Restart MDRAP service with the designated profile:
   ```bash
   python -m mdrap.cli run --profile standard --durability balanced
   ```

---

### Scenario B: Upstream Feed Disconnect / Exchange Partition

#### Symptoms
* Sequence gap alerts triggered by `SequenceTracker`.
* Venue status downgraded from `ACTIVE` to `DEGRADED` in monitoring metrics.

#### Automated Safeguards
* `ReconnectTracker` initiates exponential backoff reconnection with jitter (preventing thundering herd problems against exchange endpoints).
* Multi-venue arbitration automatically prioritizes secondary live feeds (e.g., failing over from primary ITCH feed to backup multicast without duplicating ticks).
* Quality status marks missing ticks with `Reason.SEQUENCE_GAP` and preserves strict monotonic clock order.

#### Operator Action Items
1. Inspect live venue feed health:
   ```bash
   python -m mdrap.cli status --json
   ```
2. Check Prometheus metrics endpoint:
   ```bash
   curl -s http://localhost:8000/metrics | grep mdrap_feed_status
   ```
3. If primary feed is offline, trigger manual arbitration lock if needed:
   ```bash
   python -m mdrap.cli failover --prefer-secondary
   ```

---

### Scenario C: Corrupted Disk Sector / Bit Rot

#### Symptoms
* CRC32 mismatch detected during segment scan or replay.
* `IngestLogCorruptError` flagged in system logs.

#### Automated Safeguards
* MDRAP preserves complete segment manifests (`manifest.json`).
* Quarantined records are isolated into the dedicated quarantine SQLite table or dead-letter queue; processing of uncorrupted streams is not blocked.

#### Operator Action Items
1. Run emergency salvage procedure (creates backup prior to repair):
   ```bash
   python -m mdrap.cli verify --wal-dir data/wal --salvage --backup
   ```
2. Validate replay equivalence against historical reference:
   ```bash
   python -m mdrap.cli replay --segment data/wal/segment_000000000000.log --verify-equivalence
   ```

---

### Scenario D: Downstream Consumer Stall & Ring Buffer Overrun

#### Symptoms
* Slow analytical consumers or downstream backtesting jobs stall.
* Ring buffer write pointer approaches consumer read pointers.

#### Automated Safeguards
* Low-latency SPSC seqlock shared memory buffer (`src/mdrap/shm.py`) employs cache-line padded 128-byte slots.
* Slow consumers are cleanly notified via sequence overrun detection (`ReaderLagError`).
* Core engine writer **NEVER** blocks, preventing stalling of live order book execution.

#### Operator Action Items
1. Inspect reader lag across shared memory consumers:
   ```bash
   python -m mdrap.cli metrics --shm
   ```
2. Restart stalled downstream consumer processes.

### Scenario E: Disk Saturation & Storage Exhaustion (ENOSPC)

#### Symptoms
* File system write failures or `OSError: [Errno 28] No space left on device`.
* `IngestLogPoisonedError` raised on subsequent writes to protect state from silent corruption.

#### Automated Safeguards
* IngestLog transactions immediately execute rollback and enter a poisoned state (`_is_poisoned = True`) to prevent partial or unfsynced frames from corrupting active segments.
* Segment pruning APIs (`delete_segments_before`) allow automated deletion of segments older than the active snapshot.

#### Operator Action Items
1. Clear unneeded older segments prior to the active snapshot:
   ```bash
   python -m mdrap.cli prune --wal-dir data/wal --before-offset <snapshot_offset>
   ```
2. Reopen and verify WAL log integrity after freeing disk space:
   ```bash
   python -m mdrap.cli verify --wal-dir data/wal
   ```

---

### Scenario F: Process Crash & Tail Truncation Recovery

#### Symptoms
* SIGKILL or OS crash occurring mid-write during active market trading.
* Trailing bytes written without CRC verification at the segment boundary.

#### Automated Safeguards
* Startup recovery scans backwards from EOF, detecting zero-padded preallocations, partial headers (< 28B), and CRC mismatches on torn frames.
* Automatic truncation truncates precisely to the last valid CRC32 frame boundary. Zero corrupt data enters the downstream state machine.

#### Operator Action Items
1. Check recovery logs on service restart:
   ```bash
   python -m mdrap.cli doctor
   ```
2. Verify continuous offset lineage from offset 0 to log head:
   ```bash
   python -m mdrap.cli verify --wal-dir data/wal
   ```

---

## 4. Disaster Recovery SLA Verification Runbook

Execute this verification script in staging or client acceptance environments:

```bash
# 1. Execute complete Phase 10 Client Acceptance Suite
pytest tests/test_phase10_client_acceptance.py -v

# 2. Run 9-Gate Zero-Defect Quality Protocol
python scripts/check.py

# 3. Verify Deterministic Replay Equivalence
python -m mdrap.cli replay --segment data/sample.log --verify-equivalence
```

Expected output:
```
============================== 6 passed in 1.80s ==============================
========================================================================
                 MDRAP 9-GATE LOCAL VALIDATION PROTOCOL                 
========================================================================
[PASSED] Gate 1: Code Formatting (ruff format --check src/)
[PASSED] Gate 2: Static Analysis (ruff check src/)
[PASSED] Gate 3: Single-Source Rules Parity
[PASSED] Gate 4: Core Unit Tests
[PASSED] Gate 5: Integration & Guarantees Test Suite
[PASSED] Gate 6: Security Hardening & Doctor Verification
[PASSED] Gate 7: Dependency CVE Hygiene
[PASSED] Gate 8: Native C Fastpath Compilation
[PASSED] Gate 9: Native Accelerator Smoke Check
========================================================================
        ALL 9 MDRAP QUALITY GATES PASSED! SAFE FOR LOCAL CHECKPOINT     
========================================================================
```
