---
name: mdrap-optimization
description: "Activates automatically when processing low-latency data feeds, modifying canonical streams, optimizing memory boundaries, or verifying cryptographic audit logs."
tools: ["mcp_backend_query_database", "mcp_terminal_*"]
---

# MDRAP Optimization & Cryptographic Verification Protocol

This skill enforces a dual-mode engineering protocol across the Market Data Reliability & Acceleration Platform (MDRAP). It provides standardized workflows for high-throughput zero-copy stream processing and real-time decoupled cryptographic audit validation.

---

## Protocol Modes Overview

| Mode | Trigger Tag | Primary Objective | Safety Constraints |
| :--- | :--- | :--- | :--- |
| **Mode 1: Low-Latency Stream Management** | `@zero-copy-streaming` | Eliminate object allocation thrashing, optimize hot-path loops, and manage async memory buffers | 100% semantic parity with pure-Python validation; bounded queues. |
| **Mode 2: Cryptographic Proof Checking** | `@cryptographic-audit` | Auto-generate step-by-step verification playbooks for SHA-256 Merkle audit chains | Zero real-time queue drops; read-only lockless database queries. |

---

## Mode 1: Low-Latency Stream Management (`@zero-copy-streaming`)

Activated when optimizing stream throughput, addressing memory pressure, or redesigning event transformation pipelines.

### 1. Allocation Thrashing Diagnostic
Evaluate all ingestion and validation loops (`gateway.py`, `engine.py`, `pipeline.py`, `fastpath.c`) for memory allocation overhead:
1. **Detect Churn Hotspots**:
   - String concatenation and repeated parsing in hot loops (replace with monotonic integer identifiers from `itertools.count()`).
   - Frequent `dict` allocations per tick (replace with slotted dataclasses or direct binary struct parsing).
   - Python garbage collector pressure: measure gen-0/gen-1 sweeps during high throughput runs using `gc.get_stats()` or `tuned_gc()`.
2. **Profile Heap Footprint**:
   ```bash
   python cli.py benchmark --events 500000 --profile --label opt_run
   ```
   Inspect `benchmarks/opt_run_profile.prof` for call counts in `__init__`, `json.dumps`, and float conversions.

### 2. Async Buffer & Zero-Copy Transformation Rules
1. **Shared Memory (SHM) Ring Buffers**:
   - Utilize seqlock single-producer single-consumer (SPSC) ring buffers (`src/shm.py`, `src/fastpath.c`) with 64-byte cache line alignment to prevent false sharing.
   - Read payloads directly via `memoryview` or `struct.unpack_from` without slicing bytes.
2. **Async Queue Backpressure & Buffer Sizing**:
   - Bound all ingestion queues (default: 1,000 to 10,000 slots).
   - If an async queue is saturated, update drop/eviction counters (`_drop_count`, `_drop_counts[venue]`) and log warnings at logarithmic intervals (e.g., drops `#1`, `#100`, `#1000`). Never silently drop frames.
3. **Write-Ahead Framing (`IngestLog`)**:
   - Write frames using pre-formatted binary headers (`FRAME_HEADER_FORMAT = ">HHQdII"`: magic, offset, timestamp, payload length, CRC32).
   - Commit records using atomic sequential appends with synchronous or checkpointed fsync policies.

### 3. Verification Checklist for Mode 1
- [ ] Heap allocations per tick inside inner loops reduced to near-zero.
- [ ] Native fastpath (`fastpath.c`) and pure-Python fallback (`$env:MDRAP_DISABLE_FASTPATH="1"`) produce identical validation verdicts.
- [ ] Tail latency measured at p50, p95, p99, and p99.9.

---

## Mode 2: Cryptographic Proof Checking (`@cryptographic-audit`)

Activated when inspecting audit lineage, verifying quarantine Merkle trees, checking SHA-256 chain continuity, or exporting compliance proofs.

### 1. Decoupled Verification Architecture
Cryptographic auditing must **never** block ingestion threads or drop network frames:
1. **Lockless Snapshot Queries**:
   - Read connections must use WAL-mode SQLite snapshots with `PRAGMA query_only = ON;` and `PRAGMA busy_timeout = 5000;`.
   - Never run unbounded hash traversals inside the synchronous pipeline thread.
2. **Dedicated Background Verification**:
   - Verification tasks must run in isolated worker threads or separate subprocesses to isolate CPU-intensive SHA-256 computations from streaming socket handlers.

### 2. Step-by-Step Verification Playbook Generation
When `@cryptographic-audit` is requested, automatically generate and execute the following playbook:

#### Step 1: Inspect Audit Chain Metadata
Query table schema and migration state using `mcp_backend_query_database` or CLI:
```sql
SELECT entry_id, timestamp, prev_root, batch_root, entry_hash, batch_size 
FROM quarantine_merkle_log 
ORDER BY entry_id DESC LIMIT 10;
```

#### Step 2: Validate Merkle Leaf Integrity
For a target batch of quarantined or canonical records:
1. Re-serialize each record canonically (`sort_keys=True`, deterministic formatting).
2. Calculate leaf SHA-256:
   $$\text{leaf\_hash}_i = \text{SHA256}(\text{record}_i)$$
3. Build the binary Merkle tree for the batch and verify:
   $$\text{batch\_root} = \text{MerkleTree}(\{\text{leaf\_hash}_i\}).\text{root}$$

#### Step 3: Verify Cryptographic Chain Continuity
Confirm chronological parent-child hash continuity across log entries:
$$\text{entry\_hash}_k = \text{SHA256}(\text{prev\_root}_k \mathbin{\Vert} \text{batch\_root}_k \mathbin{\Vert} \text{timestamp}_k)$$
$$\text{prev\_root}_{k} == \text{entry\_hash}_{k-1}$$

#### Step 4: Execute Standalone Verification Command
Run the verification playbook via `mcp_terminal_*`:
```bash
python -m pytest tests/test_audit_chain.py -v
```
Or execute CLI audit export:
```bash
python cli.py audit verify --db data/mdrap.db --format json
```

### 3. Failure Quarantine Protocol
If any hash mismatch or chain discontinuity is encountered:
1. **Never alter existing records**. Flag the inconsistency with investigative reason code `SECURITY_REJECT`.
2. Generate an immutable tamper-detection incident report containing:
   - Offending `entry_id` / `event_id`.
   - Expected `entry_hash` vs computed `entry_hash`.
   - Timestamp and affected feed source.

---

## Tool Integration Reference

### `mcp_backend_query_database`
Use to inspect persistent storage without acquiring exclusive write locks:
- Query `canonical_events`, `quarantine`, and `quarantine_merkle_log`.
- Verify record counts, conflict counters, and checkpoint offsets.
- Always append `LIMIT` clauses to avoid memory spikes.

### `mcp_terminal_*`
Use to run profiling benchmarks, load tests, and verification scripts:
- `python cli.py benchmark ...` for timed throughput and latency measurements.
- `python -m pytest tests/test_audit_remediation.py -v` for regression verification.
- `$env:MDRAP_DISABLE_FASTPATH="1"` cross-verification for pure-Python fallback parity.
