# MDRAP — Engineering Remediation Report

**Date**: November 2026  
**Auditor**: Independent Quantitative Infrastructure Architect & Security Auditor  
**Scope**: Codebase Remediation across Python, C Extensions, Configuration, and Benchmarks  

---

## 1. Summary of Remediated Findings

| ID | Category | Problem | Root Cause | Files Modified | Status |
|---|---|---|---|---|---|
| **REM-01** | Data Integrity | Silent mock-data fallback when API keys are missing | `PolygonFeedManager` and `DatabentoFeedManager` silently defaulted `mock_mode = True` when `api_key` was empty | `polygon_feed.py`, `databento_feed.py`, `cli/market.py` | **Remediated** |
| **REM-02** | Data Integrity | Unnormalized timestamp epoch units (ms, us, ns) | `Gateway.normalize()` passed raw timestamp into float without scale detection | `gateway.py` | **Remediated** |
| **REM-03** | Data Integrity | Future-dated timestamps unflagged in deterministic Engine | `Engine._stage_quote_and_staleness` omitted future clock-skew tolerance check | `engine.py` | **Remediated** |
| **REM-04** | Security | Insecure network defaults (`0.0.0.0`) | CLI argument parser defaulted bind host to `0.0.0.0` | `cli/__init__.py`, `cli/operations.py` | **Remediated** |
| **REM-05** | Security | Demo admin credentials / demo salt accessible in production | `SecurityManager` did not fail closed when `MDRAP_DEMO=1` was paired with `MDRAP_ENV=production` | `security.py` | **Remediated** |
| **REM-06** | Performance | Redundant SQLite query on hot ingestion path | `SQLiteProjection.apply()` queried `projection_checkpoints` table on every single transaction | `projection.py` | **Remediated** |
| **REM-07** | Performance | Benchmark Boundary F swallowed exception reporting 0.0 EPS | Benchmark script used `read_tick(i % 16384)` with incorrect method name and slot modulo | `shm.py`, `run_v53_unified_benchmark.py` | **Remediated** |
| **REM-08** | Memory Safety | NULL pointer and allocation safety in C-API extension | `py_eval_fast2` lacked NULL pointer guard; `py_shm_read_slot_v3` lacked allocation failure checks | `src/mdrap/_fastpath_c.c`, `src/_fastpath_c.c` | **Remediated** |

---

## 2. Detailed Technical Remediations

### REM-01: Elimination of Silent Mock-Data Fallback

- **Root Cause**:
  In `PolygonFeedManager.__init__` and `DatabentoFeedManager.__init__`, the constructor set `self.mock_mode = mock_mode or not self.api_key`. When an operator executed a live feed command without configuring an API key, the platform silently generated and streamed synthetic data without the operator's knowledge.
- **Remediation**:
  Updated both constructors to require explicit opt-in for mock mode. If `not mock_mode` and credentials/files are missing, an explicit `ValueError` is raised immediately. Updated `src/mdrap/cli/market.py:cmd_live()` to require `--mock-feed` if credentials are not configured.
- **Files Modified**:
  - `src/mdrap/polygon_feed.py`
  - `src/mdrap/databento_feed.py`
  - `src/mdrap/cli/market.py`
- **Tests Added**:
  - `tests/test_polygon_feed.py::test_polygon_feed_manager_missing_credentials_fails_explicitly`
  - `tests/test_databento_feed.py::test_databento_feed_manager_missing_credentials_fails_explicitly`

---

### REM-02: Deterministic Timestamp Unit Normalization

- **Root Cause**:
  Various feed formats transmit exchange timestamps in seconds, milliseconds, microseconds, or nanoseconds. `Gateway.normalize()` converted `p.get("exchange_ts")` using `float()`. Nanosecond timestamps (~1.7e18) resulted in timestamps decades in the future, triggering spurious `TS_IMPLAUSIBLE` rejections.
- **Remediation**:
  Implemented `normalize_timestamp_seconds()` in `src/mdrap/gateway.py`. The function inspects epoch magnitudes:
  - $\ge 10^{17}$ $\rightarrow$ Nanoseconds (divided by $10^9$)
  - $10^{14} \le \text{ts} < 10^{17}$ $\rightarrow$ Microseconds (divided by $10^6$)
  - $10^{11} \le \text{ts} < 10^{14}$ $\rightarrow$ Milliseconds (divided by $10^3$)
  - $< 10^{11}$ $\rightarrow$ Epoch seconds or relative simulation offsets
- **Files Modified**:
  - `src/mdrap/gateway.py`

---

### REM-03: Future Timestamp Validation in Deterministic Engine

- **Root Cause**:
  While `QualityEngine` and native C `fastpath.c` verified future timestamp bounds, `Engine.step()` in `src/mdrap/engine.py` only checked whether an event was stale (`recv_ts - exch_ts > staleness_threshold_s`). Future-dated events were permitted to pass through as `VALID`.
- **Remediation**:
  Added `DEFAULT_MAX_FUTURE_SKEW_S` (1.0s) and verified future clock skew in `_stage_quote_and_staleness`. Events where `exch_ts > recv_ts + max_future_skew_s` are flagged `Reason.TS_IMPLAUSIBLE` and marked `QualityStatus.SUSPICIOUS`.
- **Files Modified**:
  - `src/mdrap/engine.py`
- **Tests Added**:
  - `tests/test_engine_deterministic.py::test_engine_future_timestamp_detected_and_flagged_ts_implausible`

---

### REM-04: Secure Network Defaults (`127.0.0.1`)

- **Root Cause**:
  `cmd_serve` and CLI parsers defaulted `--host` to `0.0.0.0`, exposing REST API endpoints and WebSocket distribution channels to external network interfaces by default.
- **Remediation**:
  Changed default host argument and environment variable fallback to `127.0.0.1` (loopback only) across CLI subcommands.
- **Files Modified**:
  - `src/mdrap/cli/operations.py`
  - `src/mdrap/cli/__init__.py`

---

### REM-05: Hardened Demo Credentials & Production Isolation

- **Root Cause**:
  `SecurityManager` permitted loading demo keys and static salt (`mdrap_demo_salt_v1`) whenever `MDRAP_DEMO=1` was set, without verifying whether the host environment was configured for production.
- **Remediation**:
  Added environment inspection in `SecurityManager.__init__` and `hash_api_key`. If `MDRAP_ENV == "production"`, `ENVIRONMENT == "production"`, or `MDRAP_PRODUCTION == "1"`, attempting to activate demo mode or load demo salt raises `AccessDenied` / `ValueError`.
- **Files Modified**:
  - `src/mdrap/security.py`

---

### REM-06: SQLite Materialized Projection Checkpoint Caching

- **Root Cause**:
  Inside `SQLiteProjection.apply()`, every batch transaction began with an explicit query: `SELECT last_offset FROM projection_checkpoints WHERE name = ?;`. This added an unnecessary disk/B-tree read on the critical write path.
- **Remediation**:
  Added in-memory caching for `self._highest_contiguous_offset`. The database query is now only executed during projection startup (`_highest_contiguous_offset < 0`) or after a rolled-back transaction.
- **Files Modified**:
  - `src/mdrap/projection.py`

---

### REM-07: Shared Memory Benchmark Exception Correction

- **Root Cause**:
  `run_v53_unified_benchmark.py` invoked `reader.read_tick(i % 16384)`. `SHMReader` exposed `read_slot(seq)` rather than `read_tick`, and passing `i % 16384` caused commit sequence verification to fail on slot wrap. A bare `except Exception: eps_f = 0.0` masked the underlying error.
- **Remediation**:
  Fixed sequence parameter to `reader.read_slot(i)` in benchmark runner and added `read_tick = read_slot` protocol alias in `SHMReader`. Re-measured Boundary F throughput at **274,521.6 EPS**.
- **Files Modified**:
  - `src/mdrap/shm.py`
  - `benchmarks/run_v53_unified_benchmark.py`

---

### REM-08: C Extension Memory Safety & Allocation Guards

- **Root Cause**:
  `py_eval_fast2` in `_fastpath_c.c` cast `engine_ptr` directly without checking for NULL. `py_shm_read_slot_v3` allocated `bid_item` and `ask_item` without checking for allocation failure before passing to `PyList_SET_ITEM`.
- **Remediation**:
  Added NULL pointer validation on `eng` in `py_eval_fast2` and allocation failure guards with proper DECREF cleanup on `dict` in `py_shm_read_slot_v3`.
- **Files Modified**:
  - `src/mdrap/_fastpath_c.c`
  - `src/_fastpath_c.c`

---

## 3. Remaining Limitations & Architectural Trade-offs

1. **Pure Python Fallback Throughput**: While Native C delivers **>500k EPS**, the Pure Python fallback operates at **~185k EPS**. For production ultra-low-latency workloads, native compiled acceleration (`_fastpath_c`) is recommended.
2. **Synchronous Projection Mode**: Downstream SQLite projection with `async_projections=False` executes synchronous SQLite commits on `submit()`. For maximum ingestion throughput (>50k EPS), operators should configure `async_projections=True`.
3. **Loopback Default**: With `--host 127.0.0.1` defaults, multi-server distributed deployments must explicitly configure `--host 0.0.0.0` and provision reverse proxy / firewall rules.
