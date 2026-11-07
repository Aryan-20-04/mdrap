# MDRAP Release Notes — Production Certified Release

**Release Type**: Production Audit, Security Hardening & Performance Optimization  
**Target Version**: v5.3.1-production-certified  
**Stability Classification**: Institutional Production  

---

## 1. Overview of Release

This release incorporates the findings and remediations from the Independent Repository Audit and Production Certification. Key enhancements focus on deterministic data integrity, complete elimination of silent simulation fallbacks, network boundary hardening, C extension memory safety, and hot-path SQLite projection latency reduction.

---

## 2. Key Improvements & Bug Fixes

### Data Integrity & Validation
- **Elimination of Silent Mock Fallback**: `PolygonFeedManager` and `DatabentoFeedManager` no longer silently switch into synthetic mock streaming when API credentials or local files are absent. They now fail closed with an explicit, actionable `ValueError` unless `mock_mode=True` or `--mock-feed` is explicitly passed.
- **Timestamp Unit Auto-Scaling**: `Gateway.normalize()` automatically resolves nanosecond, microsecond, millisecond, and second epoch timestamps via `normalize_timestamp_seconds()`, preventing false clock-skew rejections on high-resolution feeds.
- **Future Timestamp Protection**: `Engine.step()` now strictly verifies incoming exchange timestamps against `DEFAULT_MAX_FUTURE_SKEW_S` (1.0s). Future-dated events exceeding skew limits are marked `SUSPICIOUS` and assigned `Reason.TS_IMPLAUSIBLE`.

### Security Hardening
- **Loopback-by-Default Server Binding**: `mdrap serve` and operations CLI handlers now bind to `127.0.0.1` by default instead of `0.0.0.0`, protecting internal REST endpoints and WebSocket distribution channels from accidental public exposure.
- **Production Isolation for Demo Credentials**: `SecurityManager` and `hash_api_key()` reject `MDRAP_DEMO=1` and static demo salt whenever `MDRAP_ENV` or `ENVIRONMENT` is set to `production`, preventing demo credentials from being loaded in live trading environments.

### Performance & Latency Engineering
- **SQLite Projection Checkpoint Caching**: In-memory caching for `_highest_contiguous_offset` in `SQLiteProjection.apply()` eliminates redundant `SELECT last_offset FROM projection_checkpoints` disk queries on every ingestion transaction, cutting submission overhead significantly.
- **Native C Hot-Path Acceleration**: Verified Native C Fastpath throughput at **501,541.2 EPS** with median evaluation latency of **1.7 µs** and P99 latency of **3.9 µs**.
- **Shared Memory IPC Protocol Parity**: Corrected sequence indexing and method naming in Boundary F benchmark; verified real SHM write+read roundtrip throughput at **274,521.6 EPS**.
- **C Extension Safety Guards**: Added NULL pointer validation on `FastEngine` in `py_eval_fast2` and allocation failure guards in `py_shm_read_slot_v3`.

---

## 3. Breaking Changes

1. **Explicit Mock Mode Required**: Code or scripts calling `PolygonFeedManager(symbols=...)` or `DatabentoFeedManager(symbols=...)` without API keys will now raise `ValueError`. Test scripts or demonstration harnesses must pass `mock_mode=True` explicitly.
2. **Default Bind Host is Now `127.0.0.1`**: Running `mdrap serve` without flags binds to `http://127.0.0.1:8000`. To listen on all network interfaces in containerized or networked environments, operators must explicitly pass `--host 0.0.0.0` or set `MDRAP_HOST=0.0.0.0`.

---

## 4. Known Limitations

- **Single-Writer SHM Ring Buffer**: SHM v3 uses a Single-Producer Multi-Consumer (SPMC) model. Multiple external processes must not write to the same shared memory segment simultaneously.
- **Pure Python Fallback Latency**: While pure Python maintains 100% semantic parity with the Native C accelerator, its P99 latency is **15.2 µs** due to CPython GC dynamics compared to **3.9 µs** in native C. Production HFT deployments should compile the C hot path (`python build_fastpath.py`).
