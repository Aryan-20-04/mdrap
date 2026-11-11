# MDRAP Consolidation — Agent 4: Trading, Integrations & Out-of-Scope Scope Review

## 1. Executive Summary

Agent 4 investigated all non-core domain modules, trading execution surfaces, and external data stubs to ensure that removing them does not impact market data reliability, currency normalization, or cross-venue reconciliation.

Key Verdicts:
1. **Trading Execution & Simulator**: Removed completely. Zero core reliability components import trading code. Read-only boundary is strengthened.
2. **SEC EDGAR**: Removed completely. Was an empty mock printing a notice.
3. **AIS Maritime Tracking (`vessel.py`)**: Removed completely. Already deprecated and split into `packages/mdrap-contrib-vessel`.
4. **Options Pricing (`options.py`)**: Removed completely. Already deprecated and split into `packages/mdrap-options`.
5. **Foreign Exchange Engine (`fx.py`)**: Removed completely. Independent inspection confirms `venues.py` handles all venue currencies without `fx.py`.

---

## 2. Detailed Module Analysis

### A. Trading CLI & Order Routing Simulator (`trading_cli.py`)
- **Location**: `src/mdrap/trading_cli.py` (776 lines)
- **Role in Legacy Code**: Implemented `cmd_backtest`, `cmd_risk`, `cmd_bars`, `cmd_portfolio`, `cmd_watchlist`, simulating an interactive trading desk.
- **Dependency Inspection**:
  - `gateway.py`: 0 imports.
  - `quality.py`: 0 imports.
  - `reconciliation.py`: 0 imports.
  - `storage.py`: 0 imports.
  - `provenance.py`: 0 imports.
  - `incidents.py`: 0 imports.
  - `replay.py`: 0 imports.
- **Impact**: Zero impact on market-data reliability. Removing it eliminates 776 lines of order-routing simulations and guarantees that no trading execution code exists in the core repository.

### B. SEC EDGAR Corporate Filings Integration
- **Location**: `cmd_edgar` in `src/mdrap/cli/integrations.py` (lines 755-761)
- **Role in Legacy Code**: Displayed a mock notification: `"SEC EDGAR research is out of core scope for the market data engine sidecar."`
- **Impact**: Removing this placeholder stub and its subparser entries in `cli/__init__.py` cleans up dead command aliases (`edgar`, `filings`, `insiders`, `research`).

### C. Maritime AIS Intelligence (`vessel.py`)
- **Location**: `src/mdrap/vessel.py` (78 lines) and `cmd_vessel` in `cli/integrations.py` (lines 763-1016)
- **Role in Legacy Code**: Tracked commercial shipping vessels and commodity tanker movements through geopolitical chokepoints.
- **Status**: Deprecated since v5.3.0 (`warnings.warn("VesselTracker and AIS vessel intelligence in mdrap.vessel are deprecated... use mdrap_vessel companion package instead.")`).
- **Core Dependencies Checked**:
  - `reconciliation.py`: Does not reference vessel tracking.
  - `fastpath.c`: Contains independent general-purpose math routines; no vessel-specific structs exist in the C fastpath kernel.
- **Disposition**: DELETE from core. The standalone package remains available in `packages/mdrap-contrib-vessel`.

### D. Derivatives Valuation & Greeks Engine (`options.py`)
- **Location**: `src/mdrap/options.py` (105 lines)
- **Role in Legacy Code**: Implemented Black-Scholes-Merton and Binomial American options pricing, Greeks, and implied volatility solvers.
- **Status**: Deprecated since v5.3.0 in favor of `packages/mdrap-options`.
- **Core Dependencies Checked**:
  - Market data normalization in `gateway.py` handles options ticks as standard `CanonicalEvent` structs with strike and expiry metadata; it never executes BSM pricing loops.
- **Disposition**: DELETE from core. The companion package remains available in `packages/mdrap-options`.

### E. Currency Normalization vs Speculative FX Matrix (`fx.py`)
- **Location**: `src/mdrap/fx.py` (116 lines)
- **Investigation**: Did cross-venue comparison or multi-currency instrument mapping rely on `fx.py`?
  - `venues.py`: Contains its own `format_currency` and venue registry supporting MIC codes (XNSE, XTSE, XEUR, XNYS, etc.) and tick sizes. It does NOT import `fx.py`.
  - `reconciliation.py`: Compares equivalent instruments by symbol and venue. It does NOT invoke `FXMatrix`.
  - `fx.py` only contained a static dictionary of hardcoded exchange rates (`INR: 88.50`, `EUR: 0.9215`, `JPY: 152.20`) and triangular arbitrage calculations.
- **Disposition**: DELETE. Real market data sidecars must never rely on hardcoded static currency rates for quantitative validation.

---

## 3. Read-Only Boundary Hardening

Removing `trading_cli.py` and associated order-handling abstractions guarantees that the codebase adheres 100% to its security specification:
- **0 Order APIs**: The codebase contains zero methods for `place_order`, `cancel_order`, `send_order`, or `modify_order`.
- **Passive Tapping Only**: MDRAP strictly observes incoming network feeds and local sockets without any outbound order channel.
