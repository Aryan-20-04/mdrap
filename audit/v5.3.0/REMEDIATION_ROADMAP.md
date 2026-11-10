# MDRAP v5.3.0 — Comprehensive Remediation Roadmap

**Document**: `audit/v5.3.0/REMEDIATION_ROADMAP.md`  
**Date**: November 10, 2026  
**Target Release**: MDRAP v5.3.0 Production Certification  
**Repository Working Directory**: `c:\Users\KIIT0001\Desktop\Projects\mdrap`  
**Remediation Branch**: `remediation/v5.3.0-audit-fixes`  

---

## Executive Overview

This roadmap defines the step-by-step, prioritized engineering actions required to transition MDRAP v5.3.0 from **CONDITIONALLY PRODUCTION READY** to **FULLY PRODUCTION CERTIFIED**.

Remediation is partitioned into four sequential stages:
- **Stage A (P0 — Critical Release Blockers)**: 18 items. Must be 100% resolved before release.
- **Stage B (P1 — High-Priority Stability & Security)**: 16 items. Addressed in immediate sprint.
- **Stage C (P2 — Architectural Refactoring & Test Expansion)**: 12 items.
- **Stage D (P3 — Code Hygiene, Warnings & Tooling)**: 8 items.

---

## Stage A: Priority 0 (P0) — Critical Release Blockers

### Item P0-01: Fix Semantic Parity Divergence for `HALT` and `STATUS` Events
- **Component**: `src/mdrap/fastpath.c`, `src/mdrap/fastpath.py`
- **Location**: [`src/mdrap/fastpath.c:677-680`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/mdrap/fastpath.c#L677-L680), [`src/mdrap/fastpath.py:1232-1240`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/mdrap/fastpath.py#L1232-L1240)
- **Problem**: Pure Python marks `HALT` and `STATUS` events as `VALID`. C fastpath marks them as `STATUS_INVALID` (`REASON_SCHEMA_VIOLATION`).
- **Fix**:
  1. In `src/mdrap/fastpath.py:1232`, map `EventType.HALT` to `2` and `EventType.STATUS` to `3`.
  2. In `src/mdrap/fastpath.c:677`, accept `ev->event_type == 2 || ev->event_type == 3` as valid event types that bypass price/quote checks.
  3. Recompile native C extensions via `python build_fastpath.py`.
- **Verification**: Add test cases to `tests/test_golden_parity.py` asserting `VALID` status across both engines.
- **Assigned Agent**: Native C Specialist (Agent 4) & Performance Engineer (Agent 3).

### Item P0-02: Remove Assertion Swallowing in Hot-Path Parity Test
- **Component**: `tests/test_fastpath.py`
- **Location**: [`tests/test_fastpath.py:81-86`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/tests/test_fastpath.py#L81-L86)
- **Problem**: Bare `except Exception: pass` swallows `AssertionError` during per-tick parity verification loop.
- **Fix**: Remove `try...except Exception: pass` around `assert res_py.quality_status == res_c.quality_status`. Catch only expected `(ValueError, KeyError)` during input normalization if necessary.
- **Verification**: Run `pytest tests/test_fastpath.py -v`. Must pass cleanly without swallowing assertion errors.
- **Assigned Agent**: Testing & Release Engineer (Agent 7).

### Item P0-03: Reconcile Canonical Version Metadata to `5.3.0`
- **Component**: `src/mdrap/_version.py`, `pyproject.toml`, `claims_manifest.json`, `README.md`, `tests/test_docs_verification.py`
- **Location**: [`src/mdrap/_version.py:8`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/mdrap/_version.py#L8), [`pyproject.toml:128`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/pyproject.toml#L128), [`audit/claims_manifest.json:2`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/audit/claims_manifest.json#L2), [`README.md:15`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/README.md#L15)
- **Problem**: Version frozen at `4.0.1` while documentation and release target `5.3.0`.
- **Fix**:
  1. Set `__version__ = "5.3.0"` in `src/mdrap/_version.py`.
  2. Update `version = "5.3.0"` in `contrib/pyproject.toml` and `compat/pyproject.toml`.
  3. Update `audit/claims_manifest.json` to `"mdrap_version": "5.3.0"`.
  4. Update `README.md` badge to `version-5.3.0-blue.svg`.
  5. Update `tests/test_docs_verification.py` assertions (`test_c04_version_consistency`) to match `"5.3.0"`.
- **Verification**: Run `python tools/check_docs.py` and `pytest tests/test_docs_verification.py -v`.
- **Assigned Agent**: Architecture Specialist (Agent 1) & Release Engineer (Agent 7).

### Item P0-04: Fix Databento TCP Stream Fragmentation Desync
- **Component**: `src/mdrap/databento_feed.py`
- **Location**: [`src/mdrap/databento_feed.py:520-534`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/mdrap/databento_feed.py#L520-L534)
- **Problem**: When a socket chunk splits a DBN record, unconsumed trailing bytes are discarded, desynchronizing the stream.
- **Fix**:
  1. Add `self._residual_buffer = bytearray()` to `DatabentoFeedManager`.
  2. In `_decode_stream_bytes`, prepend `self._residual_buffer` to `chunk`.
  3. When `consumed <= 0`, save `chunk[offset:]` into `self._residual_buffer` and exit chunk loop.
  4. Clear `self._residual_buffer` when all bytes are consumed.
- **Verification**: Add unit test in `tests/test_databento_feed.py` feeding split DBN byte chunks across multiple calls.
- **Assigned Agent**: Market Data Specialist (Agent 2).

### Item P0-05: Implement TLS and CRAM Handshake for Databento Client
- **Component**: `src/mdrap/databento_feed.py`
- **Location**: [`src/mdrap/databento_feed.py:560-584`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/mdrap/databento_feed.py#L560-L584)
- **Problem**: Transmits API key in plaintext over raw TCP and ignores CRAM challenge.
- **Fix**:
  1. Wrap socket with `ssl.create_default_context().wrap_socket(s, server_hostname=host)`.
  2. Parse the greeting challenge banner.
  3. Compute CRAM HMAC response (`hmac.new(key, challenge, sha256).hexdigest()`) before sending subscription parameters.
- **Verification**: Unit test greeting challenge parsing and HMAC computation in `tests/test_databento_feed.py`.
- **Assigned Agent**: Market Data Specialist (Agent 2) & Security Specialist (Agent 6).

### Item P0-06: Fix Journal Replication Server Silent Data Drop
- **Component**: `src/mdrap/journal_replicator.py`, `src/mdrap/ingestlog.py`
- **Location**: [`src/mdrap/journal_replicator.py#L224-L230`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/mdrap/journal_replicator.py#L224-L230)
- **Problem**: Server expects `target_log.append_raw()` which `IngestLog` lacks; drops payloads while sending `MSG_ACK`.
- **Fix**:
  1. Implement `append_raw(self, frame_bytes: bytes) -> int` in `src/mdrap/ingestlog.py` to append pre-framed binary records directly to the active segment with CRC validation.
  2. In `JournalReplicationServer._handle_client`, verify `self.target_log.append_raw()` succeeds before sending `MSG_ACK`. Send `MSG_ERROR` on failure.
  3. Update `tests/test_journal_replication.py` to test against a real `IngestLog` instance.
- **Verification**: Run `pytest tests/test_journal_replication.py -v`.
- **Assigned Agent**: Persistence Specialist (Agent 5).

### Item P0-07: Fix Engine WAL Recovery on Pruned Segments
- **Component**: `src/mdrap/engine.py`
- **Location**: [`src/mdrap/engine.py:843, 942, 992, 1017`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/mdrap/engine.py#L843)
- **Problem**: Recovery methods call `self.log.iter_from(0)` which crashes with `RecoveryError` once retention has purged older segments.
- **Fix**:
  1. In `recover_projections`, call `self.log.iter_from(start_off)`.
  2. In `_replay_projection_batch`, resolve latest candidate snapshot at or before `from_offset` and replay from that snapshot offset.
  3. In `subscribe` and `rebuild_projection`, handle `earliest_offset > 0` by replaying from `earliest_offset` with snapshot restoration.
- **Verification**: Add test in `tests/test_projection.py` executing recovery after `delete_segments_before()`.
- **Assigned Agent**: Persistence Specialist (Agent 5).

### Item P0-08: Restore Missing `backends.py` in `mdrap-strategies`
- **Component**: `packages/mdrap-strategies/mdrap_strategies/`
- **Location**: [`packages/mdrap-strategies/mdrap_strategies/__init__.py:1596-1601`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/packages/mdrap-strategies/mdrap_strategies/__init__.py#L1596-L1601)
- **Problem**: `backends.py` is missing from disk, breaking `import mdrap_strategies`.
- **Fix**: Implement `packages/mdrap-strategies/mdrap_strategies/backends.py` defining `ExecutionBackend`, `HistoricalSimulationBackend`, `LivePaperTradingBackend`, and `ExchangeTestnetBackend`.
- **Verification**: Execute `python -c "import mdrap_strategies; print('OK')"` and run `pytest tests/test_backtest.py -v`.
- **Assigned Agent**: Quantitative Specialist (Agent 8).

### Item P0-09: Synchronize Pipeline Batches with Lock
- **Component**: `src/mdrap/pipeline.py`
- **Location**: [`src/mdrap/pipeline.py:388-396, 1147-1158`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/mdrap/pipeline.py#L388-L396)
- **Problem**: Background writer thread reassigns batch lists while ingest thread appends without locks.
- **Fix**:
  1. Add `self._batch_lock = threading.Lock()` in `Pipeline.__init__`.
  2. Acquire `self._batch_lock` inside `_enqueue_canonical`, `_enqueue_quarantine`, `_enqueue_lineage`.
  3. In `flush()`, acquire `self._batch_lock` while swapping `self._canonical_batch, self._quarantine_batch, self._lineage_batch` with empty lists.
- **Verification**: Run `pytest tests/test_pipeline_integration.py -v`.
- **Assigned Agent**: Native C & Concurrency Specialist (Agent 4).

### Item P0-10: Fix MSVC Build Breakages in `_fastpath_c.c` and `mdrap_core.c`
- **Component**: `src/_fastpath_c.c`, `src/mdrap/_fastpath_c.c`, `src/mdrap/mdrap_core.c`
- **Location**: [`src/_fastpath_c.c:515-574`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/_fastpath_c.c#L515-L574), [`src/mdrap/mdrap_core.c:102-124`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/mdrap/mdrap_core.c#L102-L124)
- **Problem**: GCC atomic built-ins break MSVC compilation in `_fastpath_c.c`; invalid `<x86intrin.h>` breaks `mdrap_core.c`.
- **Fix**:
  1. In `_fastpath_c.c`, add `#if defined(_MSC_VER)` shims using `_InterlockedExchange64` and `_InterlockedCompareExchange64`.
  2. In `mdrap_core.c`, guard `<x86intrin.h>` behind `#if !defined(_MSC_VER)` and use `<intrin.h>` with `__cpuid` on MSVC.
- **Verification**: Run `python build_fastpath.py` and verify zero compilation errors.
- **Assigned Agent**: Native C Specialist (Agent 4).

### Item P0-11: Purge Committed Binaries from Git Tracking
- **Component**: Repository Root, `.gitignore`
- **Location**: `src/mdrap/_fastpath_native.dll`, `src/mdrap/mdrap-core.exe`
- **Problem**: Precompiled binary blobs tracked in Git.
- **Fix**:
  1. Run `git rm --cached src/mdrap/_fastpath_native.dll src/mdrap/mdrap-core.exe`.
  2. Verify `.gitignore` contains `*.dll`, `*.so`, `*.dylib`, `*.pyd`, `*.exe`.
- **Verification**: Run `git status` to confirm binaries are untracked.
- **Assigned Agent**: Release Engineer (Agent 7) & Adversarial Reviewer (Agent 9).

### Item P0-12: Remove Non-Portable `-march=native` from Wheel Build Flags
- **Component**: `src/mdrap/build_fastpath.py`
- **Location**: [`src/mdrap/build_fastpath.py:152, 301`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/mdrap/build_fastpath.py#L152)
- **Problem**: `-march=native` bakes runner CPU instructions into release wheels.
- **Fix**: Replace `-march=native` with `-O3` and a portable target baseline (`-march=x86-64-v3` or baseline `x86-64` with runtime CPU feature dispatch).
- **Verification**: Build wheel and verify portable instruction sets.
- **Assigned Agent**: Release Engineer (Agent 7).

### Item P0-13: Implement Genuine Assertion in `scripts/gen_claims.py`
- **Component**: `scripts/gen_claims.py`
- **Location**: [`scripts/gen_claims.py:155-157`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/scripts/gen_claims.py#L155-L157)
- **Problem**: `--check` flag never validates extracted claim against actual test run.
- **Fix**: Assert `claim_str in current_claim`, exiting with code 1 upon mismatch.
- **Verification**: Run `python scripts/gen_claims.py --check`.
- **Assigned Agent**: Release Engineer (Agent 7).

### Item P0-14: Synchronize Benchmark Documentation with Raw Data
- **Component**: `README.md`, `docs/benchmarks.md`
- **Location**: [`README.md:154-166`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/README.md#L154-L166), [`docs/benchmarks.md:60-74`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/docs/benchmarks.md#L60-L74)
- **Problem**: Published numbers inflate Native C throughput by 22.7% and Python by 13.5%; cites wrong reproduce command.
- **Fix**:
  1. Update table to match `audit/v5.3/raw/benchmarks_raw.json`: Native C at **416.3k EPS** (1.70 µs p50), pure Python at **149.9k EPS**, true speedup **2.78x**.
  2. Change reproduction command for isolated C kernel from `bench_scale.py` to dedicated C benchmark script.
  3. Clarify that 21.6k EPS durability reflects batch-100 writes, while per-tick synchronous fsync achieves 270 EPS.
- **Verification**: Run `python tools/check_docs.py` to confirm documentation numbers match raw JSON files.
- **Assigned Agent**: Performance Engineer (Agent 3) & Adversarial Reviewer (Agent 9).

### Item P0-15: Fix Missing Contract Classes in Options Fallback Shim
- **Component**: `src/mdrap/options.py`
- **Location**: [`src/mdrap/options.py:46-60`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/mdrap/options.py#L46-L60)
- **Problem**: `trading_cli.py` imports `OptionContract`, `OptionType`, `ExerciseStyle`, `price_option`, which do not exist in the fallback stub.
- **Fix**: Define stub classes and functions in `src/mdrap/options.py` that raise `CompanionPackageMissing("mdrap_options")`.
- **Verification**: Test `from mdrap.options import OptionContract, OptionType, ExerciseStyle, price_option`.
- **Assigned Agent**: Architecture Specialist (Agent 1).

### Item P0-16: Fix Fail-Closed Invariant in `src/mdrap/backtest.py`
- **Component**: `src/mdrap/backtest.py`
- **Location**: [`src/mdrap/backtest.py:100-101`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/mdrap/backtest.py#L100-L101)
- **Problem**: Crashes with `AttributeError` when `runner.strategy` does not exist on stub `StrategyRunner`.
- **Fix**: Guard `runner.strategy.on_start()` with checks that `runner.strategy` is valid, or raise `CompanionPackageMissing("mdrap_strategies")` cleanly.
- **Verification**: Run `python -c "from mdrap.backtest import BacktestEngine; BacktestEngine().run(None, [])"` and confirm graceful handling.
- **Assigned Agent**: Architecture Specialist (Agent 1).

### Item P0-17: Eliminate Silent 1.0 Parity in `src/mdrap/fx.py`
- **Component**: `src/mdrap/fx.py`
- **Location**: [`src/mdrap/fx.py:86-89`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/mdrap/fx.py#L86-L89)
- **Problem**: Unknown currencies silently convert at 1:1 parity with USD.
- **Fix**: Raise `KeyError(f"Unmapped currency: {currency}")` instead of returning 1.0.
- **Verification**: Run `pytest tests/test_fx.py -v`.
- **Assigned Agent**: Quantitative Specialist (Agent 8).

### Item P0-18: Declare `websockets` Dependency in `pyproject.toml`
- **Component**: `pyproject.toml`
- **Location**: [`pyproject.toml:39-88`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/pyproject.toml#L39-L88)
- **Problem**: `websockets` omitted from dependencies, causing live feeds to fail on standard installs.
- **Fix**: Add `"websockets>=12.0,<17.0"` to `[project.optional-dependencies]` under `feeds` and to `dev` / `test` dependency groups.
- **Verification**: Run `pip install -e ".[test]"` and verify `import websockets` succeeds.
- **Assigned Agent**: Release Engineer (Agent 7).

---

## Stage B: Priority 1 (P1) — High-Priority Stability & Security

- **P1-01**: Fix `/metrics` authentication bypass via `X-Forwarded-For` spoofing (`src/mdrap/api.py:1670-1678`).
- **P1-02**: Correct RFC 6962 domain separation in `packages/mdrap-audit` by prepending `0x01` byte to internal node concatenations.
- **P1-03**: Enforce fail-closed WebSocket revocation check in `api.py:376` (`if not ent:`).
- **P1-04**: Add gap healing in `AsyncProjectionWorker` when queue saturation occurs (`projection.py:1129`).
- **P1-05**: Implement heartbeat keepalive frames (`MSG_HEARTBEAT`) every 500 ms in journal replication daemon (`journal_replicator.py:170`).
- **P1-06**: Remove fabricated Level 2 depth ladders from Yahoo Finance equity quotes in `live.py:855`.
- **P1-07**: Add token-bucket rate limiting and HTTP 429 backoff handling to `live.py:1032`.
- **P1-08**: Update `ALLOWED_TOP_SECTIONS` in `config_loader.py:56` to support `bbo`, `feeds`, `columnar`, `watchdog`.
- **P1-09**: Decouple `trading_cli.py` from `rich` by importing from internal `mdrap.term`.
- **P1-10**: Update `.github/workflows/ci.yml` to run test suite under `MDRAP_DISABLE_FASTPATH="1"`.
- **P1-11**: Update `.github/workflows/wheels.yml` to build and upload source distribution (`sdist`).
- **P1-12**: In `mdrap_core.c`, publish head sequence on every tick or after microsecond timeout.
- **P1-13**: Protect `replay_ring` in `fastpath.c:1019` with lock or seqlock.
- **P1-14**: Pass training data to strategy fitting callback in `backtest.py:370` or document sliding window test.
- **P1-15**: Bridge `InstitutionalRiskEngine` pre-trade controls into core `risk.py`.
- **P1-16**: Retitle `24H_SOAK_TEST_REPORT.md` to `BURST_SOAK_AND_STABILITY_REPORT.md`.

---

## Stage C: Priority 2 (P2) — Architectural Refactoring & Test Expansion

- **P2-01**: Bound `_inst_map` in `FastQualityEngine` with LRU capacity limit (`fastpath.py:1101`).
- **P2-02**: Correct SHM slot size in certification docs (128 bytes = 2.0 MB).
- **P2-03**: Implement dedicated unit tests for `network.py`, `supervisor.py`, `profile.py`, `gap.py`, `adapters/`.
- **P2-04**: Harden SHM test fixtures with deterministic `unlink` in `tests/test_shm.py`.
- **P2-05**: Remove dead modules `trading_cli.py`, `shm_drainer.py`, `scheduler.py`, legacy `journal.py`.
- **P2-06**: Clean up maritime dead struct `_CFastChokepoint` from `fastpath.py`.
- **P2-07**: Replace hardcoded demo auth token `"mdrap_demo_pro_key"` in `workload_simulator.py`.
- **P2-08**: Centralize test fixtures in `tests/conftest.py`.

---

## Stage D: Priority 3 (P3) — Code Hygiene & Tooling

- **P3-01**: Add compiler warning and security flags (`-Wall -Wextra -fstack-protector-strong`) in `build_fastpath.py`.
- **P3-02**: Sanitize `.env.example` template (remove wildcard CORS).
- **P3-03**: Update `SECURITY.md` supported versions to include 5.x.
- **P3-04**: Format repository with `ruff format` and clean all lint warnings.

---
*Roadmap approved and ready for execution by Lead Engineering Orchestrator.*
