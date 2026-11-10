# Architecture & System Design — Recommendations

## Priority 0 (Release Blockers)
1. **Reconcile Package Version Metadata**:
   - Update `__version__ = "5.3.0"` in `src/mdrap/_version.py:8`.
   - Update version assertions in `tests/test_docs_verification.py`.
   - Update version in `audit/claims_manifest.json` and `README.md` badge.
   - Run `tools/check_docs.py` to confirm alignment.
2. **Fix Missing Contract Classes in `src/mdrap/options.py`**:
   - Define `OptionContract`, `OptionType`, `ExerciseStyle`, `price_option` in `src/mdrap/options.py` raising `CompanionPackageMissing("mdrap_options")`.
3. **Fix Fail-Closed Invariant in `src/mdrap/backtest.py`**:
   - Guard `runner.strategy.on_start()` with checks that `runner.strategy` exists.

## Priority 1 (Architecture & Stability)
4. **Unify Configuration Schema**:
   - Consolidate on `mdrap.toml` as single source of truth.
   - Add `bbo`, `feeds`, `columnar`, `watchdog` to `ALLOWED_TOP_SECTIONS` in `src/mdrap/config_loader.py`.
5. **Decouple `trading_cli.py` from Third-Party `rich`**:
   - Import `Console`, `Table`, `Panel` from internal `mdrap.term`.
   - Log warnings instead of swallowing errors when optional CLI modules fail to import.

## Priority 2 (Code Hygiene)
6. **Clean Up Companion Stubs in Core**:
   - Implement clean `__getattr__` on companion stub modules that consistently raises `CompanionPackageMissing`.
   - Remove dead code `_CORE_MODULES` in `src/mdrap_build.py`.
7. **Fix Shadowed Struct in `src/mdrap/eurex.py`**:
   - Delete dead line 27.\n