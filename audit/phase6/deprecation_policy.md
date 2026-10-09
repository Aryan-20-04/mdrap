# MDRAP Phase 6 — Deprecation Policy, Module Lifecycle, and Sunset Runbook

## 1. Executive Summary & Purpose
As MDRAP evolves from a generalist prototype into an institutional-grade, low-latency market data infrastructure engine, non-core peripheral modules (such as downstream analytics, option pricer models, and legacy monolithic scripts) must be retired or separated to preserve the ultra-lean core engine footprint (`/ponytail` principle).

This document establishes the official **Deprecation and Sunset Policy**, outlining notice windows, migration paths, and versioning rules.

---

## 2. Deprecation Governance & Timelines

MDRAP adheres strictly to **Semantic Versioning 2.0.0 (SemVer)**:
1. **Deprecation Notice Window**: Any public API or module scheduled for removal must remain deprecated for a minimum of **2 minor releases** or **180 calendar days**, whichever is longer.
2. **Runtime Warnings**: Deprecated interfaces must emit Python `DeprecationWarning` or `FutureWarning` log messages specifying the exact replacement API and target removal version.
3. **No Silent Breaking Changes**: Breaking API removals are strictly confined to Major version increments ($v1.x \rightarrow v2.0$).
4. **Zero Silent Behavioral Changes**: Behavior changes to existing APIs during minor/patch versions must be opt-in via configuration flags.

---

## 3. Deprecated Modules & Phase 7 Extraction Roadmap

| Deprecated Module | Current Path | Status in Phase 6 | Planned Phase 7 Replacement / Destination | Sunset Target |
| :--- | :--- | :--- | :--- | :--- |
| **Non-Core Options Engine** | `src/options.py` | Deprecated (Tier 3) | Extracted to standalone package `mdrap-options` | MDRAP v2.0.0 |
| **TCA Transaction Cost** | `src/tca.py` | Deprecated (Tier 3) | Extracted to standalone package `mdrap-analytics` | MDRAP v2.0.0 |
| **Legacy Strategy SDK** | `src/strategy_sdk.py` | Deprecated (Tier 3) | Extracted to `mdrap-strategies` repo | MDRAP v2.0.0 |
| **Vessel Protocol Adapter** | `src/vessel.py` | Deprecated (Tier 3) | Extracted to `mdrap-contrib-vessel` | MDRAP v2.0.0 |
| **Monolithic Single Pipeline**| `src/pipeline.py` | Maintained for backward compat | Replaced by `src/partition.py` sharded cluster | MDRAP v2.0.0 |

---

## 4. Deprecation Code Annotation Standard

When marking an interface as deprecated, developers must use the standard decorator pattern:

```python
import warnings

def deprecated(replacement: str, removal_version: str = "2.0.0"):
    """Decorator to mark interfaces as deprecated."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            warnings.warn(
                f"{func.__name__} is deprecated and will be removed in MDRAP {removal_version}. "
                f"Use {replacement} instead.",
                category=DeprecationWarning,
                stacklevel=2,
            )
            return func(*args, **kwargs)
        return wrapper
    return decorator
```

### CI Gate for Deprecation
CI quality gates monitor deprecation notices to verify that:
- Every deprecated function contains an explicit alternative in its docstring.
- Tests exercise deprecated interfaces with `pytest.deprecated_call()` assertions.
- Deprecated code does not appear on the hot trading path.
