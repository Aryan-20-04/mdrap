# MDRAP Phase 7 — Authoritative Invariant Catalog

## 1. Executive Summary & Catalog Scope
This catalog defines the definitive mathematical and operational invariants governing MDRAP. Every invariant specifies its exact behavioral guarantee, enforcing modules, failure impacts, existing test anchors, newly added Phase 7 tests, and automated CI quality gate enforcement mechanisms.

---

## 2. Definitive Invariant Catalog

### INV-01: Zero Silent Data Loss
- **Exact Guarantee**: No valid, suspicious, or invalid event shall ever be dropped from the system without incrementing an observable counter, logging a warning, or routing to the quarantine audit log.
- **Enforcing Modules**: [`src/gateway.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/gateway.py), [`src/ws_feed.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/ws_feed.py), [`src/partition.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/partition.py)
- **Conditions**: All operating conditions, including backpressure saturation and feed errors.
- **Known Exceptions**: None.
- **Failure Impact**: Critical (undetected missing ticks causing false pricing and financial loss).
- **Existing Tests**: `test_no_silent_drops_under_backpressure`, `test_consumer_fanout_and_noisy_neighbor_eviction`.
- **Phase 7 Enhancement**: Property-based fuzzing with synthetic queue saturation.
- **CI Enforcement**: Blocking assertion in `tests/test_phase7_verification.py`.

### INV-02: Strict Monotonic Sequencing per Partition
- **Exact Guarantee**: For any two consecutive events $e_i, e_{i+1}$ published on a partition, their sequence numbers satisfy $\text{Seq}(e_{i+1}) = \text{Seq}(e_i) + 1$, with zero gaps, decrements, or duplicates.
- **Enforcing Modules**: [`src/gateway.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/gateway.py), [`src/journal.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/journal.py), [`src/partition.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/partition.py)
- **Conditions**: All live ingestion, partitioned routing, and crash recovery replay.
- **Known Exceptions**: None within an active partition.
- **Failure Impact**: Fatal (corrupted order book state and downstream order matching failures).
- **Existing Tests**: `test_partitioned_routing_and_isolation`.
- **Phase 7 Enhancement**: Metamorphic replay tests comparing live sequence vs replayed sequence.
- **CI Enforcement**: Programmatic check in `historical_verifier.py`.

### INV-03: Quality Status Priority Monotonicity
- **Exact Guarantee**: Event quality statuses obey the strict priority ordering: $\text{INVALID} > \text{SUSPICIOUS} > \text{VALID}$. An event evaluated as INVALID or SUSPICIOUS can never be downgraded to a lower severity.
- **Enforcing Modules**: [`src/quality.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/quality.py), [`src/rules.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/rules.py), [`src/fastpath.c`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/fastpath.c)
- **Conditions**: Multi-rule evaluation passes across trades and quotes.
- **Known Exceptions**: None.
- **Failure Impact**: Severe (publishing erroneous prices as valid).
- **Existing Tests**: `test_rules.py`, `test_quality.py`.
- **Phase 7 Enhancement**: Property tests generating random invalid price/size combinations.
- **CI Enforcement**: Differential testing against native C bitmask oracle.

### INV-04: Lineage & Provenance Preservation
- **Exact Guarantee**: Every canonical event and canonical decision must preserve its original venue source ID, exchange timestamp, receive timestamp, and reason codes.
- **Enforcing Modules**: [`src/models.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/models.py), [`src/reconciliation.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/reconciliation.py)
- **Conditions**: Ingress, normalization, reconciliation, and archival.
- **Known Exceptions**: Feeds lacking exchange timestamps fall back to `receive_timestamp` with `clock_source = "GATEWAY_RECV"`.
- **Failure Impact**: High (inability to satisfy regulatory trade reconstructability audits).
- **Existing Tests**: `test_reconciliation.py`.
- **Phase 7 Enhancement**: Round-trip SBE serialization preserving 100% of provenance attributes.
- **CI Enforcement**: Golden vector byte assertions.

### INV-05: WAL Durability Boundary Before Publication
- **Exact Guarantee**: An event is never published to client sockets or marked as processed until its record is safely appended to the IngestLog WAL.
- **Enforcing Modules**: [`src/journal.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/journal.py), [`src/pipeline.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/pipeline.py), [`src/partition.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/partition.py)
- **Conditions**: Live trading mode.
- **Known Exceptions**: `:memory:` ephemeral test mode.
- **Failure Impact**: Fatal (phantom fills published to algorithms before crash).
- **Existing Tests**: `test_journal.py`, `test_shm_drainer.py`.
- **Phase 7 Enhancement**: Crash fault-injection verifying zero uncommitted events are recoverable.
- **CI Enforcement**: Automated fault-injection suite.

### INV-06: Strict Resource & Buffer Boundedness
- **Exact Guarantee**: All internal queues, fan-out buffers, seqlock rings, and deduplication LRUs must have fixed upper memory ceilings. Memory delta must plateau under sustained load.
- **Enforcing Modules**: [`src/partition.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/partition.py), [`src/shm.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/shm.py), [`src/quality.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/quality.py)
- **Conditions**: Continuous operation under peak load.
- **Known Exceptions**: None.
- **Failure Impact**: Fatal (OOM killer termination of production service).
- **Existing Tests**: `test_burst_throughput_saturation_and_latency_profile`.
- **Phase 7 Enhancement**: 20k event soak test verifying RSS growth $\le 6.0\text{ MB}$.
- **CI Enforcement**: Memory budget assertion in CI gate.

### INV-07: Mutex-Free Read Concurrency in Shared Memory
- **Exact Guarantee**: Shared-memory readers never acquire locks and cannot stall or block writer threads, guarded by atomic seqlock version counters.
- **Enforcing Modules**: [`src/shm.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/shm.py), [`src/fastpath.c`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/fastpath.c)
- **Conditions**: Multi-process local IPC stream distribution.
- **Known Exceptions**: None.
- **Failure Impact**: Severe (slow consumer causing latency spike in publisher).
- **Existing Tests**: `test_shm.py`, `test_shm_decoupled.py`.
- **Phase 7 Enhancement**: Concurrent stress test with stalled readers.
- **CI Enforcement**: Unit test assertion checking zero writer sleep duration.

### INV-08: Pure Python Fallback Semantic Parity
- **Exact Guarantee**: Pure Python fallback execution must yield identical validation outcomes, reason bitmasks, and normalized fields as native C extensions.
- **Enforcing Modules**: [`src/quality.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/quality.py), [`src/fastpath.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/fastpath.py), [`src/fastpath.c`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/fastpath.c)
- **Conditions**: Operating on platforms where C compiler is unavailable or `$env:MDRAP_DISABLE_FASTPATH="1"`.
- **Known Exceptions**: None.
- **Failure Impact**: High (inconsistent execution between development and production).
- **Existing Tests**: `benchmarks/run_differential_5m.py`.
- **Phase 7 Enhancement**: Automated differential test suite comparing 10,000 synthetic events.
- **CI Enforcement**: Dual-run CI gate with and without C acceleration.

### INV-09: Single-Writer Exclusive Filesystem Fencing
- **Exact Guarantee**: A partition directory can have only one active writer process. A secondary launch attempting to bind the same directory immediately terminates with exit code 42.
- **Enforcing Modules**: [`src/partition.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/partition.py), OS kernel file locks (`shard.lock`)
- **Conditions**: Process startup and multi-instance orchestration.
- **Known Exceptions**: None.
- **Failure Impact**: Fatal (split-brain corruption of SQLite databases and WAL segments).
- **Existing Tests**: Failure drill `FAIL-FENCE-COLLISION`.
- **Phase 7 Enhancement**: Model-based state transition verification.
- **CI Enforcement**: Automated fencing collision test.

### INV-10: Cryptographic Immutability of Cold Archives
- **Exact Guarantee**: Sealed WAL archives and Parquet partitions must have SHA-256 Merkle root hashes recorded in an immutable manifest. Any altered byte must be flagged as tampering.
- **Enforcing Modules**: [`src/journal.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/journal.py), [`src/archive.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/archive.py)
- **Conditions**: EOD compaction and historical forensic retrieval.
- **Known Exceptions**: None.
- **Failure Impact**: High (inability to satisfy SEC 17a-4 compliance requirements).
- **Existing Tests**: `test_archive.py`.
- **Phase 7 Enhancement**: Historical integrity verification tool (`historical_verifier.py`).
- **CI Enforcement**: Automated corrupt-archive detection test.
