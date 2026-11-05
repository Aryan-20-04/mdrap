# MDRAP v5.0 Release Risk Register

## 1. Executive Summary

This risk register enumerates the technical, architectural, operational, and deployment risks identified during the independent quality audit of MDRAP v5.0 on branch `v5.0-development`.

---

## 2. Risk Matrix

| Risk ID | Category | Description | Likelihood | Impact | Severity | Current Mitigation / Resolution Status |
|:---:|:---|:---|:---:|:---:|:---:|:---|
| **RISK-01** | **Performance** | Redundant `json.dumps()` in `IngestLog.append_batch` consumes 20.9% of ingestion pipeline CPU. | High | Medium | **MODERATE** | Identified in cProfile. Scheduled for Phase 5 optimization via pre-serialized payloads or fast byte assembly. |
| **RISK-02** | **Performance** | Unconditional `_current_file.flush()` in `IngestLog.append_batch` triggers OS `WriteFile` transitions on every tick even under `fsync_policy="never"`. | High | Medium | **MODERATE** | Identified in profiling (0.089s cost). Scheduled for Phase 5 optimization to rely on standard stream buffers under `never`. |
| **RISK-03** | **Performance** | Double `_current_file.tell()` per tick in `IngestLog.append_batch` incurs file descriptor handle overhead. | High | Low | **LOW** | Can track byte offset in memory (`self._current_segment_bytes`) rather than querying OS file pointer. |
| **RISK-04** | **Packaging** | Version string in `src/mdrap/_version.py` remains `4.0.1` while branch is `v5.0-development`. | High | Medium | **MODERATE** | Version bump to `5.0.0` scheduled as final release step following Phase 6 optimization validation. |
| **RISK-05** | **Durability** | Power loss during mid-sector disk writes could create torn WAL frames. | Low | Critical | **LOW (Mitigated)** | Mitigated by SSE4.2 CRC32C frame checksums; `IngestLog.recover()` truncates back to last valid frame. |
| **RISK-06** | **Concurrency** | Slow shared-memory readers may be lapped by ultra-high-speed publishers during market volatility bursts. | Medium | Medium | **LOW (Mitigated)** | Mitigated by Seqlock double-read verification, `SHM_FLAG_WATERMARK_WARNING` bitflag, and `SHMOverrunStats.total_laps`. |
| **RISK-07** | **Operational** | Disk space exhaustion if high-speed feeds run without periodic snapshot compaction or WAL segment pruning. | Medium | High | **MODERATE** | Managed by `min_wal_snapshots_retained=2`, `max_snapshots_retained=3`, and `wal_autocheckpoint=10000`. |
| **RISK-08** | **Portability** | Free-threaded Python (PEP 703) could expose lack of explicit memory barriers in pure-Python `SpscRingQueue`. | Low | Medium | **LOW** | CPython GIL protects integer stores; native C kernel uses explicit memory barriers. |
