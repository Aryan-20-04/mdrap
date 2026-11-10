# Quantitative, Trading Risk & Strategy — Detailed Findings

## Executive Summary
Audited market microstructure engines, algorithmic strategy SDK, risk management, real-time analytics, and wire protocol decoders. L2 consolidated depth (`depth.py`), L3 MBO (`mbo.py`), ITCH 5.0 (`itch.py`), and SBE wire decoders (`sbe.py`, `cme.py`, `eurex.py`) are fully implemented and mathematically rigorous. However, a CRITICAL broken import in `packages/mdrap-strategies` prevents that package from loading, and high-severity defects exist in backtesting, core pre-trade risk, and FX conversion.

## Categorized Findings

### CRITICAL
1. **F-QNT-01: Broken Import in `packages/mdrap-strategies` Prevents Module Loading**:
   - `packages/mdrap-strategies/mdrap_strategies/__init__.py:1596-1601`:
     ```python
     from .backends import (ExecutionBackend, HistoricalSimulationBackend, LivePaperTradingBackend, ExchangeTestnetBackend)
     ```
   - The file `backends.py` is **missing from the filesystem**.
   - **Impact**: `import mdrap_strategies` crashes with `ModuleNotFoundError`. `src/mdrap/strategy_sdk.py` falls back to stub classes. `tests/test_backtest.py` is completely skipped.

### HIGH
2. **F-QNT-02: Walk-Forward Backtesting Incomplete / Discards 60% of Data Without Model Fitting**:
   - `src/mdrap/backtest.py:370-377`: Partitions window into `train_events` and `test_events`.
   - `train_events` is completely discarded; no parameter optimization or fitting occurs.
   - **Impact**: Provides an illusion of walk-forward optimization while merely running a sliding test window on 40% of the data.

3. **F-QNT-03: Missing Pre-Trade Risk Enforcement & Hollow `PositionLimits` in Core**:
   - `src/mdrap/risk.py:378-385`: `PositionLimits` is an orphaned dataclass with zero integration into order generation or validation. Core has no pre-trade risk gates.

4. **F-QNT-04: Silent 1.0 Parity Fallback on Unknown Currencies in FX Engine**:
   - `src/mdrap/fx.py:86-89`: If a currency pair is unmapped, silently returns `1.0` parity.
   - **Impact**: Unmapped currencies (e.g. JPY, KRW) are valued 1:1 with USD, distorting portfolio valuations.

### MEDIUM
5. **F-QNT-05: `VolatilityTracker` Window Parameter Ignored (Expanding Lifetime Variance)**:
   - `src/mdrap/analytics.py:178-212`: Stores `self.window`, but computes lifetime expanding Welford variance across the entire process lifetime instead of a rolling window.

6. **F-QNT-06: FIX Engine Lacks Socket Layer, Session State Machine, and Gap Recovery**:
   - `src/mdrap/fix_engine.py:160-259`: Overwrites sequence numbers without gap checks; lacks 35=2 Resend Request handling.

7. **F-QNT-07: Indeterminate Event Tie-Breaking in Backtester**:
   - `src/mdrap/backtest.py:82`: Sorts events purely by `exchange_timestamp` without secondary tie-breaker on sequence number.\n