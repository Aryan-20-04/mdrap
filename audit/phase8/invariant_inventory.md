# MDRAP Phase 8 Invariant Inventory

## 1. Non-Negotiable Core Invariants (Section 1.1 Specification)

The MDRAP platform guarantees 16 fundamental architectural invariants across all execution paths:

| ID | Invariant Name | Contract Specification | Enforcement Boundary | Verification Test / Mechanism |
|---|---|---|---|---|
| **INV-01** | **Zero Silent Drops** | No market event is ever dropped without an observable counter increment, log entry, and quarantine/error record. | `gateway.py`, `ws_feed.py`, `service.py`, `partition.py` | `tests/test_hardening.py`, `tests/test_ws_feed.py` |
| **INV-02** | **Monotonic Ingestion Sequencing** | Every ingested event receives a strictly monotonic 64-bit integer sequence ID per channel/partition. | `itertools.count()` in `gateway.py`, `partition.py` | `tests/test_gateway.py`, `tests/test_partition.py` |
| **INV-03** | **Immutable Raw Data** | Raw ingest events are immutable after capture; normalization produces derived records preserving raw references. | `RawEvent` dataclass with frozen slots | `tests/test_gateway.py` |
| **INV-04** | **Strict Quality Precedence** | Status priority order is strictly `INVALID > SUSPICIOUS > VALID`. Once marked INVALID, an event cannot be upgraded. | `quality.py`, `fastpath.c` bitmask evaluation | `tests/test_quality.py`, `tests/test_fastpath.py` |
| **INV-05** | **Pure Python Parity** | Pure Python implementation must produce identical validation status, reason bitmasks, and metrics as native C. | `quality.py` vs `fastpath.c` dual-engine | `tests/test_fastpath.py`, `tests/test_quality.py` |
| **INV-06** | **Deterministic Replay** | Replaying an event stream from identical raw data must yield the exact same canonical state and decisions. | Fixed PRNG seeds (`seed=42`), monotonic clocks | `tests/test_replay.py`, `tests/test_repro.py` |
| **INV-07** | **Authoritative WAL Persistence** | The `IngestLog` WAL is the authoritative persistence gate; SQLite is an asynchronous derived projection. | `src/mdrap/ingestlog.py` binary journal writer | `tests/test_ingestlog.py`, `tests/test_shm_drainer.py` |
| **INV-08** | **Single Writer Ownership** | Only one active leader or writer process may append to a partition WAL at any given time. | Process-local lock and monotonic epoch token | `src/consensus.py`, `src/partition.py` |
| **INV-09** | **Bounded Memory Allocation** | All queues, ring buffers, and caching structures have fixed, pre-allocated upper size bounds. | `queue.Queue(maxsize=N)`, fixed seqlock ring buffers | `tests/test_system_limitations.py`, `tests/test_partition.py` |
| **INV-10** | **Observable Backpressure & Eviction** | Slow consumers that exceed buffer capacity are shed with explicit metrics; producer threads are never blocked. | Non-blocking `put_nowait`, eviction thresholds | `tests/test_hardening.py`, `tests/test_async_fanout.py` |
| **INV-11** | **Cryptographic Lineage Audit** | Historical log entries have verifiable CRC32 frame checksums and SHA-256 Merkle root chains. | `src/historical_verifier.py`, `src/storage.py` | `tests/test_historical_verifier.py` |
| **INV-12** | **Provenance Preservation** | Every canonical decision documents its input source, timestamp, competing prices, and selection rule. | `CanonicalDecision` in `reconciliation.py` | `tests/test_reconciliation.py` |
| **INV-13** | **Stale Leader Fencing** | Any write attempted with an epoch token lower than the partition's active generation is rejected. | Monotonic epoch guard at WAL write gate | `tests/test_ha_consensus.py` |
| **INV-14** | **Transport Neutrality** | Business logic is decoupled from transport (SHM, TCP socket, WebSocket, SBE wire format). | `CanonicalEvent` domain model separation | `tests/test_models.py`, `tests/test_shm_decoupled.py` |
| **INV-15** | **Tail Latency Sensitivity** | Engine telemetry tracks and reports $p50$, $p95$, $p99$, $p99.9$, and max latencies, never just averages. | `TelemetryCollector` running percentiles | `benchmarks/run_benchmarks.py` |
| **INV-16** | **Security & Access Isolation** | API authentication, role-based access control, and rate limiting are enforced before event delivery. | `src/security.py` HMAC tokens & RBAC | `tests/test_security.py` |

## 2. Invariant Governance Sign-Off
All 16 invariants are formally inventoried and actively guarded by the continuous test regression suite. Phase 8 workstreams must maintain or strengthen each of these invariants without exception.
