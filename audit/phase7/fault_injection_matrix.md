# MDRAP Phase 7 — Comprehensive Fault-Injection Matrix & Test Protocol

## 1. Executive Summary & Protocol Design
A system that functions solely under ideal laboratory conditions is unfit for institutional market-data operations.

This matrix formalizes the **Phase 7 Fault-Injection Protocol**, specifying exact failure injections, expected state transitions, permitted and forbidden outcomes, assertions, and recovery validation procedures.

---

## 2. Comprehensive Fault-Injection Scenarios

### Scenario FI-01: Partial Write / Trailing Byte Truncation
- **Target Subsystem**: IngestLog Write-Ahead Log (`src/journal.py`).
- **Initial State**: 10,000 events committed to `shard_0.wal`.
- **Injected Fault**: Process abruptly terminates mid-append; trailing 48 bytes of the final record are truncated/zeroed.
- **Expected Transition**: Engine crashes; during restart, `BinaryJournalReader` scans to the last fully committed record, detects an incomplete frame, logs a warning, and truncates the uncommitted tail.
- **Permitted Outcomes**: Clean truncation at event 9,999; exit code 0 on recovery.
- **Forbidden Outcomes**: Crash with unhandled exception, corruption of earlier records, or processing the truncated frame.
- **Recovery Assertion**: Exactly 9,999 records recovered with 100% valid CRC32 checksums.

### Scenario FI-02: Corrupted Payload CRC32 Checksum
- **Target Subsystem**: Recovery & Archival Validation (`src/journal.py`).
- **Initial State**: Sealed WAL segment containing 5,000 historical events.
- **Injected Fault**: 1 byte in the middle of frame 2,500 is flipped from `0x42` to `0x43`, invalidating the frame CRC32.
- **Expected Transition**: Historical verifier flags frame 2,500 as corrupted; isolates segment; alerts operators.
- **Permitted Outcomes**: Explicit `ChecksumError` raised with byte offset and sequence number.
- **Forbidden Outcomes**: Silent acceptance of the altered record or silent omission without logging.
- **Recovery Assertion**: CRC32 mismatch detected with zero sequence corruption of uncorrupted records.

### Scenario FI-03: Slow Consumer Socket Backpressure
- **Target Subsystem**: Decoupled Bounded Fan-Out (`src/partition.py`).
- **Initial State**: Shard processing 15,000 eps with 5 active consumers.
- **Injected Fault**: Consumer #3 pauses TCP reads completely for 5 seconds.
- **Expected Transition**: Consumer #3 queue fills to capacity (1,000 frames); drop counter increments; after 10 drops, client is evicted and socket closed.
- **Permitted Outcomes**: Consumers #1, #2, #4, #5 experience zero dropped ticks and zero latency jitter.
- **Forbidden Outcomes**: Head-of-line blocking stalling the ingest engine or memory growth $> 10\text{ MB}$.
- **Recovery Assertion**: Client evicted at drop #10; engine throughput maintained $\ge 15,000\text{ eps}$.

### Scenario FI-04: Concurrent Partition Ownership Collision
- **Target Subsystem**: Partition Fencing (`src/partition.py`).
- **Initial State**: Primary Shard 0 actively processing events with `shard.lock` held.
- **Injected Fault**: Misconfigured secondary process attempts to launch targeting Shard 0.
- **Expected Transition**: Secondary process attempts non-blocking lock acquisition, fails, logs a critical error, and exits immediately.
- **Permitted Outcomes**: Secondary exits with code `42 (ERR_PARTITION_LOCKED)`.
- **Forbidden Outcomes**: Secondary starts processing or overwrites primary WAL files.
- **Recovery Assertion**: Primary process runs completely undisturbed.

### Scenario FI-05: Storage Drainer Disk Backpressure
- **Target Subsystem**: SQLite Batched Store (`src/storage.py`).
- **Initial State**: Active ingest streaming events to in-memory drainer queue.
- **Injected Fault**: Simulated disk write freeze (100 ms fsync pause).
- **Expected Transition**: Batch queue expands up to configured ceiling; memory bounds enforced; once disk clears, batch commits resume.
- **Permitted Outcomes**: IngestLog WAL continues atomic appends; zero ticks lost.
- **Forbidden Outcomes**: Process crash or silent dropping of unpersisted events.
- **Recovery Assertion**: All queued batches flushed successfully upon disk restoration.
