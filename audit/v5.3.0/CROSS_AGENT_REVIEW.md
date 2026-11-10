# MDRAP v5.3.0 — Cross-Agent Independent Audit Synthesis & Contradiction Resolution

**Document**: `audit/v5.3.0/CROSS_AGENT_REVIEW.md`  
**Date**: November 10, 2026  
**Lead Orchestrator**: Multi-Agent Lead Engineering Orchestrator  
**Participating Agents**:
1. Agent 1: Architecture & System Design
2. Agent 2: Market Data & Live Feed Integrity
3. Agent 3: Performance & Latency Engineering
4. Agent 4: Native C, SIMD & Concurrency
5. Agent 5: Persistence, WAL & Data Integrity
6. Agent 6: Security & Threat Modeling
7. Agent 7: Testing, Build & Release Engineering
8. Agent 8: Quantitative, Trading Risk & Strategy
9. Agent 9: Independent Adversarial Reviewer

---

## 1. Cross-Agent Convergence & Multi-Disciplinary Corroborations

The multi-agent audit revealed extraordinary convergence on several systemic issues where multiple independent agents discovered the same root causes through completely different analysis angles:

### 1.1 The Version Dissociation (4.0.1 vs 5.3.0)
- **Flagged by**: Agent 1 (F-ARCH-01), Agent 7 (F-TST-04), Agent 9 (F-ADV-01).
- **Consensus**: The codebase is officially package version `4.0.1` (`src/mdrap/_version.py`, `pyproject.toml`, `audit/claims_manifest.json`, `/health` API), but all public documentation (`CHANGELOG.md`, `README.md`, `FINAL_RELEASE_CHECKLIST.md`, and master certification documents) claims `v5.3.0`.
- **Root Cause Analysis**: Remediation cycles for versions 5.0 through 5.3 introduced advanced strategies and analytics in `packages/mdrap-strategies`, and documented them in `CHANGELOG.md`, but the root package version was deliberately frozen at `4.0.1` to avoid breaking `test_c04_version_consistency` in `tests/test_docs_verification.py`.
- **Resolution**: Canonical version must be unified to `5.3.0` across all metadata, with test assertions and document check scripts updated in lockstep.

### 1.2 Committed Windows Binary Artifacts in Git
- **Flagged by**: Agent 1 (F-ARCH-04), Agent 4 (F-NAT-03), Agent 6 (F-SEC-01), Agent 9 (F-ADV-03).
- **Consensus**: `src/mdrap/_fastpath_native.dll` and `src/mdrap/mdrap-core.exe` are committed precompiled PE/DLL binaries.
- **Root Cause Analysis**: Convenience binaries were placed in `src/mdrap/` to allow Windows developers without C compilers to use native acceleration. However, committing unverifiable binaries to Git is a severe supply chain vulnerability (CWE-506).
- **Resolution**: Remove both binaries from Git tracking, add `*.dll`, `*.so`, `*.pyd`, `*.exe` to `.gitignore`, and rely exclusively on `build_fastpath.py` and GitHub Actions CI wheels.

### 1.3 How Did Critical Bugs Pass 1,734 Tests? (The Test Masking Web)
A key question for the Lead Orchestrator: *If the codebase has severe defects, how did the test suite report 1,734 passed tests with 0 failures?*
The cross-agent investigation uncovered a web of mutual test masking:

1. **Why didn't tests catch the HALT/STATUS C-Fastpath Parity Failure (Agent 3 F-PERF-01)?**
   - **Answer (Agent 7 F-TST-01)**: In `tests/test_fastpath.py:81-86`, the per-tick comparison loop was wrapped in `try: assert ... except Exception: pass`, which swallowed `AssertionError`!
   - In addition, `tests/test_golden_parity.py` and `tests/test_parity_differential.py` only generated `TRADE` and `QUOTE` events, never testing `HALT` or `STATUS`.
2. **Why didn't tests catch the Broken Import in `mdrap-strategies` (Agent 8 F-QNT-01)?**
   - **Answer (Agent 7 & Agent 9)**: `tests/test_backtest.py` wrapped tests in `pytest.importorskip("mdrap_strategies")`. When the import failed, pytest silently **skipped** the tests rather than failing!
3. **Why didn't tests catch the Journal Replication Silent Data Drop (Agent 5 F-PER-01)?**
   - **Answer (Agent 5)**: `tests/test_journal_replication.py:44-46` used a dummy `MockTargetLog` class that artificially defined `append_raw()`, masking the fact that the real `IngestLog` lacked this method!
4. **Why didn't CI catch documentation count mismatches (Agent 7 F-TST-02)?**
   - **Answer (Agent 7)**: `scripts/gen_claims.py --check` never compared `current_claim` to `claim_str`; it unconditionally printed `[PASSED]`.

---

## 2. Contradiction Resolution & Nuance Analysis

### 2.1 Benchmark Claims: What Are the Real Numbers?
- **The Controversy**: `README.md` claims Native C Fastpath reaches **510,820 EPS** (1.60 µs p50) with a **3.00x speedup** over pure Python at **170,080 EPS** (8.30 µs p50). It also claims the isolated C kernel reaches **43.67M EPS** (22.9 ns p50 / 45.0 ns p99).
- **Adversarial & Performance Evidence**:
  1. In `audit/v5.3/raw/benchmarks_raw.json`, the authoritative run recorded:
     - Native C Fastpath: **416,331.2 EPS** (1.70 µs p50)
     - Pure Python Fallback: **149,885.7 EPS** (4.40 µs p50)
     - True Measured Speedup: **2.78x**
  2. In `benchmarks/stage_breakdown.json`, p50, p95, p99, and max are all identical (`22.8969 ns`). The 45.0 ns p99 in `README.md` was manually fabricated.
  3. `benchmarks/bench_scale.py` tests Python `Engine.submit()` at 20k–50k EPS, not the 43M EPS C kernel.
- **Resolution**:
  - Ground truth is `audit/v5.3/raw/benchmarks_raw.json`.
  - Update `README.md` and documentation to report the actual measured numbers: **416.3k EPS** Native C, **149.9k EPS** Python, **2.78x speedup**.
  - Remove the fabricated 45.0 ns p99 claim.
  - Fix the reproduction command to point to the actual isolated C micro-benchmark.

### 2.2 Is Market Data Real or Synthetic?
- **The Controversy**: Does MDRAP genuinely process real-time market data or is it synthetic?
- **Agent 2 & Agent 9 Findings**:
  - **Crypto Feeds**: `ws_feed.py` and `live.py` have **genuine live connections** to Coinbase, Binance, Kraken, OKX, and Bybit. These are real WebSocket/REST feeds.
  - **Equity Feeds**: Yahoo Finance fetches real consolidated prices, but manufactures **synthetic Level 2 depth ladders** (100–500 shares).
  - **Polygon.io & Databento**: Contain real wire decoders, but live TCP/WS streams default or silently fall back to synthetic mock streams.
  - **Default Pipeline Mode**: Default configurations run on `FeedSimulator` (synthetic).
- **Resolution**:
  - Live crypto feed capabilities are **genuine and verified**.
  - Must explicitly document that the default pipeline runs on synthetic data unless external live feed flags are passed.
  - Remove fabricated L2 depth from Yahoo Finance equity quotes.
  - Fix Databento TCP stream fragmentation so live Databento feeds actually work.

### 2.3 Disk Durability: 21,615 EPS vs 270 EPS
- **The Controversy**: `README.md` claims "Disk Durability (Always)" delivers **21,615 EPS**.
- **Adversarial & Performance Evidence**:
  - In `docs/audit/v5/PERFORMANCE_REGRESSION_REPORT.md`, true tick-by-tick synchronous `fsync` achieves **270 EPS** ($3,195.5\,\mu\text{s}$ latency).
  - The 21.6k EPS figure was obtained using **batch-100 writes** (`batch_size: 100`).
- **Resolution**:
  - Clarify in documentation: synchronous tick-by-tick fsync achieves ~270 EPS (hardware bound on SSD write cycles); batched fsync (batch=100) achieves 21.6k EPS; true group-commit achieves 29.6k EPS.

---

## 3. Unified Severity Consensus Matrix

Across all 9 agents, findings were consolidated, duplicates merged, and final severities assigned:

| Global ID | Component | Severity | Finding Summary | Originating Agents |
| :--- | :--- | :---: | :--- | :--- |
| **U-01** | `src/mdrap/fastpath.c`, `fastpath.py` | **P0 (CRITICAL)** | Semantic divergence: `HALT` and `STATUS` events evaluate as `VALID` in Python but rejected as `INVALID` in C fastpath. | Agent 3 |
| **U-02** | `tests/test_fastpath.py` | **P0 (CRITICAL)** | Bare `except Exception: pass` swallows `AssertionError` during per-tick parity verification loop. | Agent 7 |
| **U-03** | `src/mdrap/_version.py`, packaging | **P0 (CRITICAL)** | Severe version drift: package is hardcoded as `4.0.1` while release documents assert `v5.3.0`. | Agent 1, 7, 9 |
| **U-04** | `src/mdrap/databento_feed.py` | **P0 (CRITICAL)** | Databento TCP stream fragmentation drops residual bytes, corrupting all subsequent binary DBN records. | Agent 2 |
| **U-05** | `src/mdrap/databento_feed.py` | **P0 (CRITICAL)** | Transmits API key in plaintext over raw TCP and discards CRAM authentication challenge. | Agent 2 |
| **U-06** | `src/mdrap/journal_replicator.py` | **P0 (CRITICAL)** | Server calls `target_log.append_raw()` which `IngestLog` lacks, dropping all frames while sending fake ACKs. | Agent 5 |
| **U-07** | `src/mdrap/engine.py` | **P0 (CRITICAL)** | Hardcoded `iter_from(0)` in 4 recovery methods crashes permanently once WAL retention prunes old segments. | Agent 5 |
| **U-08** | `packages/mdrap-strategies/` | **P0 (CRITICAL)** | Broken import `from .backends import ...` crashes package import because `backends.py` is missing. | Agent 8 |
| **U-09** | `src/mdrap/pipeline.py` | **P0 (CRITICAL)** | Race condition: background writer thread flushes and reassigns batch lists while ingest thread appends to them. | Agent 4 |
| **U-10** | `src/_fastpath_c.c`, `mdrap_core.c`| **P0 (CRITICAL)** | MSVC build breakages: missing atomic builtins in `_fastpath_c.c` and invalid GCC headers in `mdrap_core.c`. | Agent 4 |
| **U-11** | `src/mdrap/_fastpath_native.dll`, `.exe` | **P0 (CRITICAL)** | Precompiled Windows binary artifacts committed directly into Git repository. | Agent 1, 4, 6, 9 |
| **U-12** | `src/mdrap/build_fastpath.py` | **P0 (CRITICAL)** | Hardcoded `-march=native` compiler flag bakes runner-specific instructions into release wheels (SIGILL risk). | Agent 7 |
| **U-13** | `scripts/gen_claims.py` | **P0 (CRITICAL)** | `--check` flag acts as a rubber stamp; never compares extracted claim against test result. | Agent 7 |
| **U-14** | `README.md`, benchmarks | **P0 (CRITICAL)** | Documentation inflates Native C EPS by 22.7%, Python by 13.5%, and cites wrong reproduction command. | Agent 3, 9 |
| **U-15** | `src/mdrap/options.py`, `trading_cli.py`| **P0 (CRITICAL)** | Missing contract classes in options fallback shim crash `mdrap options price` with `ImportError`. | Agent 1 |
| **U-16** | `src/mdrap/backtest.py` | **P0 (CRITICAL)** | `BacktestEngine.run()` crashes with `AttributeError` on stub `StrategyRunner`. | Agent 1 |
| **U-17** | `src/mdrap/fx.py` | **P0 (CRITICAL)** | Silent 1.0 parity fallback on unknown currencies causes severe multi-currency valuation errors. | Agent 8 |
| **U-18** | `pyproject.toml` | **P0 (CRITICAL)** | Missing `websockets` dependency causes `ws_feed` to fail and `polygon_feed` to fall back to mock. | Agent 2 |
| **U-19** | `src/mdrap/api.py` | **P1 (HIGH)** | `/metrics` auth bypass via `X-Forwarded-For` spoofing behind trusted reverse proxies. | Agent 6 |
| **U-20** | `packages/mdrap-audit/` | **P1 (HIGH)** | Broken RFC 6962 domain separation: internal node hashes omit required `0x01` byte prefix. | Agent 6 |
| **U-21** | `src/mdrap/api.py` | **P1 (HIGH)** | Incomplete WebSocket token revocation check in `broadcast_event()`. | Agent 6 |
| **U-22** | `src/mdrap/projection.py` | **P1 (HIGH)** | `AsyncProjectionWorker` drops events on queue saturation, causing permanent gap-assertion deadlocks. | Agent 5 |
| **U-23** | `src/mdrap/journal_replicator.py` | **P1 (HIGH)** | Replication daemon disconnects every 2 seconds during quiet periods due to missing keepalive heartbeats. | Agent 5 |
| **U-24** | `src/mdrap/live.py` | **P1 (HIGH)** | Fabricated Level 2 depth ladders in Yahoo Finance equity feed. | Agent 2 |
| **U-25** | `src/mdrap/live.py` | **P1 (HIGH)** | Unbounded 20 Hz REST polling without HTTP 429 rate limit backoff. | Agent 2 |
| **U-26** | `src/mdrap/config_loader.py` | **P1 (HIGH)** | Config schema divergence: rejects `bbo`, `feeds`, `columnar`, `watchdog` top-level sections. | Agent 1 |
| **U-27** | `src/mdrap/trading_cli.py` | **P1 (HIGH)** | Direct import of `rich` causes 12 subcommands to silently disappear in stdlib-only environments. | Agent 1 |
| **U-28** | `.github/workflows/ci.yml` | **P1 (HIGH)** | Fallback parity in CI runs only 1 test file under `MDRAP_DISABLE_FASTPATH="1"`, leaving 303 unverified. | Agent 7 |
| **U-29** | `.github/workflows/wheels.yml` | **P1 (HIGH)** | Release workflow publishes binary wheels but omits source distribution (`sdist`). | Agent 7 |
| **U-30** | `src/mdrap/mdrap_core.c` | **P1 (HIGH)** | Head sequence published only every 32 ticks, adding up to 31 ticks of unbounded IPC latency. | Agent 4 |
| **U-31** | `src/mdrap/fastpath.c` | **P1 (HIGH)** | Data race and torn reads in Native Replay Buffer (`replay_ring`). | Agent 4 |
| **U-32** | `src/mdrap/backtest.py` | **P1 (HIGH)** | Walk-forward backtesting discards 60% of data without model fitting. | Agent 8 |
| **U-33** | `src/mdrap/risk.py` | **P1 (HIGH)** | `PositionLimits` in core is a hollow un-enforced dataclass. | Agent 8 |
| **U-34** | `docs/audit/v5/24H_SOAK_TEST_REPORT.md`| **P1 (HIGH)** | 32.7-second soak run mislabeled as "24-Hour Continuous Soak Test". | Agent 9 |

---

## 4. Remediation Stage Plan

The 34 unified issues will be resolved systematically across 4 staged phases:
- **Stage A (P0 Blockers)**: Issues U-01 through U-18 (Parity divergence, assertion swallowing, version unification, Databento TCP/TLS, replication drop, Engine WAL recovery, missing backends.py, pipeline batch locking, MSVC compatibility, binary purging, wheel compiler flags, claims gate, benchmark documentation, options/backtest stubs, FX parity, websockets dependency).
- **Stage B (P1 High-Priority Stability & Security)**: Issues U-19 through U-34 (Metrics spoofing, Merkle domain separation, WebSocket revocation, projection worker gap healing, replication heartbeats, fabricated L2 removal, HTTP 429 backoff, config schema alignment, rich decoupling, CI fallback parity, sdist publishing, head publishing, replay ring locking, walk-forward clarification, soak report retitling).
- **Stage C (P2 Architectural Cleanups & Tests)**: Issues related to memory bounds, unit test coverage gaps for network/supervisor, dead code elimination, and conftest fixtures.
- **Stage D (P3 Code Hygiene & Tooling)**: Formatting, compiler warnings, and documentation polish.

---
*Cross-agent review and contradiction resolution signed off by Lead Engineering Orchestrator.*
