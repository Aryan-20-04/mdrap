# MDRAP Engineering Public Claim Ledger

**Milestone:** v3.1.1 Production-Readiness Remediation  
**Baseline Git Commit:** `4c5a0d8141f60a4fcf82ca3ed3885e679563e9ba` (HEAD of `main`)  
**Audit Baseline Date:** 2026-10-15  
**Review Policy:** Every public claim made in `README.md`, `CHANGELOG.md`, `docs/status.md`, and marketing summaries must link to reproducible, executable evidence. Unsupported or false claims must be formally withdrawn or qualified.

---

## Public Claim Inventory & Verification Audit

| Claim ID | Quoted Claim Text | Source Document & Line | Current Verification Status | Defect Reference | Remediation Action & Phase |
|---|---|---|---|---|---|
| **CLM-01** | *"Distributed Safety & Fast Failover: Quorum-based lease coordination and epoch-fenced storage boundaries preventing split-brain writes"* | `README.md:17`, `docs/status.md:30` | **UNSUPPORTED / FALSE** | F-01 | Withdraw distributed consensus claims in Phase 1; scope as single-node engine. |
| **CLM-02** | *"Sub-110ms Failover Lifecycle (p50: 105.01 ms, p95: 118.42 ms, p99: 124.80 ms across 100 trials)"* | `README.md:17`, `docs/status.md:32` | **FALSE / FABRICATED METHODOLOGY** | F-02, F-03 | Withdraw failover latency figures in Phase 1; they derive from a single-process `time.sleep(0.1)` timer. |
| **CLM-03** | *"Mode B: Independent-Host Staging & Distributed Safety — VERIFIED"* | `docs/status.md:12` | **FALSE** | F-01, F-02 | Remove "Independent-Host Verified" claim in Phase 1; testing was strictly local loopback / in-process. |
| **CLM-04** | *"Lossless IngestLog Durability... segment CRC32C checksums"* | `README.md:13`, `docs/status.md:26` | **MISLEADING / INACCURATE** | F-06 | Correct terminology to IEEE CRC-32 (zlib standard) in Phase 1 & 3; cover frame headers. |
| **CLM-05** | *"100% Acknowledged-Write Recovery... across abrupt power loss and SIGKILL chaos drills"* | `README.md:13`, `docs/status.md:27` | **PARTIAL / ATOMICTY GAP** | F-04, F-05 | Qualify: batch append is non-atomic across rotations (F-04). Verify true atomicity in Phase 3. |
| **CLM-06** | *"High-Throughput Network Fanout... 32,404.7 frames/s peak throughput with 100.0% delivery rate"* | `README.md:18`, `docs/status.md:37` | **CONTAMINATED SAMPLES** | F-11 | Benchmark counted background simulator traffic leading to > 100% delivery (F-11). Re-measure in Phase 7. |
| **CLM-07** | *"1,240 Automated Tests: 100% clean test execution"* | `docs/status.md:21` | **SCOPED OBSERVATION** | F-12 | Tested on Windows baseline (`1,275 passed`); Linux baseline has 3 SHM failures and skips when native C lib is absent. State exact platform and environment in Phase 1. |
| **CLM-08** | *"Lock-free SPSC shared memory ring buffer (`shm.py`)"* | `README.md:16` | **INACCURATE NAMING** | F-07 | Uses seqlock protocol with monotonic commit sequences, not pure lock-free. On POSIX, restart recovery fails due to unlinked inode retention. Fix in Phase 2. |
| **CLM-09** | *"Never Silently Discard Bad Data"* | `README.md:15` | **PARTIALLY TRUE** | F-08, F-09 | While core quality engine quarantines INVALID events, malformed input can crash the background ingestion thread (F-08), and missing/unknown event types are accepted as VALID (F-09). Fix in Phase 4 & 5. |
| **CLM-10** | *"AVX2 SIMD acceleration"* | `README.md:16` | **SUPPORTED (C Hot Path)** | N/A | C kernel uses vector compiler flags / intrinsics for fast path decoding when compiled with gcc/clang. Document platform dependencies. |

---

## Required Remediation Actions Summary

1. **Phase 1 Action:** Remove CLM-01, CLM-02, CLM-03 from `README.md` and `docs/status.md`. Retitle `docs/status.md` to reflect single-node embedded architecture. Correct CRC32C references in CLM-04 to IEEE CRC-32.
2. **Phase 2 Action:** Address CLM-08 by fixing POSIX restart recovery in `src/mdrap/shm.py`.
3. **Phase 3 Action:** Address CLM-05 by implementing atomic batch commit and rollback in `src/mdrap/ingestlog.py`.
4. **Phase 4 & 5 Action:** Address CLM-09 by isolating ingestion loop crashes and validating event types strictly.
5. **Phase 7 Action:** Address CLM-06 by isolating test sequence IDs in fan-out benchmarks and bounding delivery at 100%.
