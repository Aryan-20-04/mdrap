# Phase 08 — Package Boundaries & Edition Separation (F-13)

- **Status:** PASS
- **Starting SHA:** `5af8e1e69dc3cf258d55ee7be95f00e998b671a5`
- **Ending SHA:** Pending Phase 8 commit
- **Environment:**
  - OS: Windows 11 AMD64 (10.0.26200-SP0)
  - Python: 3.13.1 (MSC v.1942 64-bit AMD64)
  - Compiler: GCC 14.2.0 (x86_64-w64-mingw32 / MSYS64)
- **Scope:**
  1. Resolve F-13 (Core Scope Sprawl): Classify all repository modules into strict tiers (Tier 1: Core Engine, Tier 2: Pluggable Adapters & Operations, Tier 3: Companion & Deprecated Extensions).
  2. Author `docs/product/edition-boundaries.md` detailing architectural invariants, interface contracts, and licensing terms (100% MIT core with zero commercial dependencies).
  3. Ensure Tier 3 companion modules (`options`, `tca`, `risk`, `strategy_sdk`, `vessel`) emit `DeprecationWarning` when imported directly from `mdrap`.
  4. Guarantee core engine (`mdrap-core`) runs and executes independent of companion analytics packages.
  5. Author automated regression tests in `tests/test_phase8_edition_boundaries.py` and update the engineering defect ledger.
- **Design Chosen and Alternatives Rejected:**
  - *Chosen (Ponytail minimal diff & standard library):*
    1. **Strict 3-Tier Classification:** Authored `docs/product/edition-boundaries.md`, formalizing `mdrap-core` as single-node, 100% MIT, pure Python + native C acceleration with zero out-of-tree dependencies.
    2. **Graceful Backward Compatibility via Deprecation Warnings:** Maintained non-breaking stubs in `src/mdrap/` for `options.py`, `tca.py`, `risk.py`, `strategy_sdk.py`, and `vessel.py` that emit standard `DeprecationWarning` on import, directing users to standalone companion packages without breaking existing code.
    3. **Zero Core Leakage:** Verified `mdrap/__init__.py` `__all__` exports strictly core market data primitives (`Engine`, `IngestLog`, `RawEvent`, `CanonicalEvent`, `QualityStatus`, `Reason`, `Pipeline`, `SQLiteProjection`, `SHMReader/Writer`).
  - *Rejected:*
    - Hard deletion of legacy files: rejected to avoid breaking downstream callers abruptly; deprecation warnings provide clean migration runway.
- **Files Changed / Created:**
  - `src/mdrap/risk.py` (Modified: added `DeprecationWarning` for non-core risk management module)
  - `docs/product/edition-boundaries.md` (Created: specification of tiers, contracts, and licensing)
  - `docs/engineering/defect-ledger.md` (Modified: marked F-13 as FIXED)
  - `tests/test_phase8_edition_boundaries.py` (Created: 3 regression tests for clean core imports, execution, and deprecation warnings)
  - `docs/engineering/phase-reports/phase-08.md` (This report)
- **Regression Tests Executed & Evidence:**
  - `pytest tests/test_phase8_edition_boundaries.py -v` (3 passed in 1.53s)
    - `test_core_engine_clean_imports_without_companion` PASSED
    - `test_core_pipeline_functional_without_companion` PASSED
    - `test_companion_modules_emit_deprecation_warnings` PASSED
  - `pytest tests/test_risk.py -v` (12 passed in 0.28s)
- **Acceptance Checklist:**
  - [x] All repository modules classified into Tier 1 Core, Tier 2 Adapters, and Tier 3 Companion.
  - [x] `docs/product/edition-boundaries.md` created with license and interface terms.
  - [x] Tier 3 modules emit `DeprecationWarning` on import.
  - [x] Core engine operates independently without companion packages.
  - [x] Defect ledger updated with F-13 marked FIXED.
- **Residual Risks:**
  - None.
- **Reviewer:** Antigravity Autonomous Agent (Odin Audit Protocol)
- **Decision and Rationale:**
  - **PASS.** All Phase 8 acceptance criteria verified. Phase 9 (Native C Memory Safety & Sanitizers) is authorized to begin.
