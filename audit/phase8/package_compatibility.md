# MDRAP Phase 8 — Package Compatibility and Migration Policy

## 1. Compatibility Policy Contract
MDRAP follows Semantic Versioning 2.0.0. To protect existing institutional client systems, Phase 8 adopts a **zero-breakage deprecation policy**:

1. **Import Forwarding**: Existing import statements targeting `mdrap.options`, `mdrap.tca`, `mdrap.strategy_sdk`, and `mdrap.vessel` (as well as legacy top-level `options`, `tca`, `strategy_sdk`, and `vessel`) continue to function cleanly.
2. **Deprecation Warnings**: When non-core modules are imported from `mdrap.*` or root, a `DeprecationWarning` is emitted indicating the module is deprecated and directing the developer to the respective companion package.
3. **Migration Window**: Deprecated shims will remain active throughout MDRAP v3.x releases. Removal will only occur in the next major version (v4.0.0).

## 2. Consumer Migration Matrix

| Legacy Import Path | Recommended Migration Path | Package Dependency |
|---|---|---|
| `from mdrap.options import OptionsChain, bsm_price` | `from mdrap_options import OptionsChain, bsm_price` | `pip install mdrap-options` |
| `from mdrap.tca import TCAEngine, ExecutionRecord` | `from mdrap_analytics import TCAEngine, ExecutionRecord` | `pip install mdrap-analytics` |
| `from mdrap.strategy_sdk import Strategy, Order` | `from mdrap_strategies import Strategy, Order` | `pip install mdrap-strategies` |
| `from mdrap.vessel import VesselTracker` | `from mdrap_vessel import VesselTracker` | `pip install mdrap-contrib-vessel` |

## 3. Backward Compatibility Verification
Automated test suite verification proves that:
- Legacy test files (`tests/test_options.py`, `tests/test_tca.py`, `tests/test_strategy_sdk.py`, `tests/test_vessel.py`) continue to pass 41/41 tests using legacy imports.
- Companion test suite (`tests/test_companion_packages.py`) passes 4/4 tests using new companion package imports.
- Total test regression passes at 100%.
