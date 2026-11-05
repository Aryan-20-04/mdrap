# MDRAP v5.0 Optimization Decisions & Execution Protocol

## 1. Executive Summary & Change-Management Rules

This document outlines the optimization decisions, candidate evaluations, and risk mitigations proposed for MDRAP v5.0 based on the empirical findings in [`TOP_10_BOTTLENECKS.md`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/audit/v5/performance/TOP_10_BOTTLENECKS.md).

> [!IMPORTANT]
> **Strict Change-Management Protocol**:
> In accordance with the engineering mandate:
> 1. No production code changes will be made until explicit user approval is granted.
> 2. Each optimization must be evaluated individually against the 12-tier benchmark baseline.
> 3. Never sacrifice validation accuracy, sequence gap detection, recovery correctness, or durability guarantees to improve a benchmark number.
> 4. If an optimization does not produce a measurable throughput gain or latency reduction, it will be reverted immediately.

---

## 2. Ranked Optimization Evaluation Plan

### Optimization Candidate 1: IngestLog Buffer Flush Decoupling (Bottleneck #2)
- **Current Behavior**: `self._current_file.flush()` is called unconditionally on every appended batch in `IngestLog.append_batch()`.
- **Proposed Optimization**: Under `fsync_policy="never"`, allow Python's underlying `BufferedRandom` stream buffer (64 KB) to buffer writes in user-space, flushing only when the buffer is full or when `IngestLog.flush()` is explicitly invoked.
- **Expected Impact**: +8–10% throughput, eliminates 10,000 OS `WriteFile` kernel transitions per 10k ticks.
- **Risk Assessment**: Low. `IngestLog.flush()` and `close()` ensure all bytes are committed before shutdowns.

### Optimization Candidate 2: In-Memory Segment Size Tracking (Bottleneck #3)
- **Current Behavior**: `self._current_file.tell()` is called twice per append in `append_batch()` to check segment capacity limits.
- **Proposed Optimization**: Maintain `self._current_segment_bytes` in memory, incrementing by `len(batch_buffer)` on each write. Synchronize with `tell()` only during segment rotation.
- **Expected Impact**: +5% throughput, eliminates 20,000 Win32 `SetFilePointerEx` syscalls per 10k ticks.
- **Risk Assessment**: Negligible. Cleanly resets on segment rotation.

### Optimization Candidate 3: Byte-Preserving Ingestion & Serialization Elimination (Bottleneck #1)
- **Current Behavior**: `IngestLog.append_batch()` unconditionally calls `json.dumps(rec).encode("utf-8")` even if the payload was already formatted or serialized as JSON.
- **Proposed Optimization**: If `RawEvent.payload` is already `bytes` (e.g. wire JSON, SBE frame, or ITCH packet), assemble the frame directly by wrapping bytes without redundant parsing and re-encoding.
- **Expected Impact**: +20–25% throughput reduction in CPU critical path.
- **Risk Assessment**: Low. Validated by roundtrip replay tests.

### Optimization Candidate 4: Heuristic Payload Size Estimation (Bottleneck #4)
- **Current Behavior**: `estimate_payload_size()` falls back to `len(json.dumps(payload))` if the estimated size approaches `max_bytes` or if payload contains sub-containers.
- **Proposed Optimization**: Cache payload length if pre-computed by gateway, or use fast recursive scalar length bounding without calling `json.dumps()`.
- **Expected Impact**: +4–5% throughput.
- **Risk Assessment**: Low. Guarded by strict upper-bound invariant assertions.

---

## 3. Implementation Phasing Strategy

```
Step 1: Obtain User Approval
       │
       ▼
Step 2: Implement Optimization Candidate 1 & 2 (IngestLog Flush + Tell Decoupling)
       ├── Run pytest tests/ (Ensure 0 regressions)
       ├── Run 12-Tier Benchmark Runner
       └── Record Scoreboard Delta
       │
       ▼
Step 3: Implement Optimization Candidate 3 (Byte-Preserving Ingestion)
       ├── Run pytest tests/ (Ensure 0 regressions)
       ├── Run 12-Tier Benchmark Runner
       └── Record Scoreboard Delta
       │
       ▼
Step 4: Execute Full 14-Stage Release Gate (scripts/release_gate.py)
       └── Final Certification Sign-Off
```
