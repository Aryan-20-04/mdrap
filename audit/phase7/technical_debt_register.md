# MDRAP Phase 7 — Technical Debt Register & Remediation Roadmap

## 1. Executive Summary & Governance Objective
Technical debt in financial market-data platforms directly translates into latent production instability, increased tail latency, and heightened maintenance costs.

This register documents all tracked technical debt items, assessing severity, consequences, proposed actions, migration risks, and scheduled resolution timelines.

---

## 2. Tracked Technical Debt Items

### TD-01: Legacy Monolithic Pipeline Co-Existence
- **Evidence**: `src/pipeline.py` coexists alongside `src/partition.py`.
- **Consequence**: Two separate ingestion paths exist in the codebase. Maintenance changes must be verified against both pipelines.
- **Severity**: MEDIUM
- **Proposed Action**: Phase 8 extraction of monolithic pipeline into legacy compatibility layer; `src/partition.py` becomes the single authoritative engine.
- **Migration Risk**: Low. Legacy single-node configurations will route through a 1-shard partition configuration.
- **Decision**: Deferred to Phase 8 / v3.0.0.

### TD-02: Non-Core Analytics Modules in Core Engine Repository
- **Evidence**: `src/options.py`, `src/tca.py`, `src/strategy_sdk.py`, `src/vessel.py` reside in `src/`.
- **Consequence**: Codebase size is bloated with downstream algorithmic execution and pricing models that are non-core to low-latency market-data normalization (`/ponytail` violation).
- **Severity**: LOW
- **Proposed Action**: Package excision: migrate into standalone companion packages (`mdrap-options`, `mdrap-analytics`, `mdrap-strategies`).
- **Migration Risk**: Low (clean deprecation warnings already emitted in Phase 6).
- **Decision**: Phase 8 scheduled extraction.

### TD-03: Windows High-Resolution Timer Tick Quantization
- **Evidence**: Tail latency spikes observed at $p99.9 = 535.5\text{ \mu s}$ on Windows 11 due to 1.0 ms / 0.5 ms OS timer interrupt resolution.
- **Consequence**: Tail latency reports reflect operating system timer quantization rather than algorithmic delays.
- **Severity**: LOW (Linux production deployments use tickless kernels and `clock_gettime(CLOCK_MONOTONIC_RAW)` with sub-microsecond precision).
- **Proposed Action**: Document OS-specific tail behavior; recommend Linux Ubuntu 22.04 LTS / RHEL 9 for tier-1 latency-sensitive production desks.
- **Decision**: Mitigated via documentation in support matrix.

### TD-04: Test Suite Deselection Coupling
- **Evidence**: 60 tests marked `@pytest.mark.slow` or `@pytest.mark.network` deselected by default in `pytest.ini`.
- **Consequence**: Requires two separate test runner configurations to distinguish fast pull request gates from comprehensive nightly soaks.
- **Severity**: LOW
- **Proposed Action**: Standardized in `scripts/run_phase7_quality_gates.py` which runs fast regression on every commit and full soak nightly.
- **Decision**: Resolved in Phase 7.
