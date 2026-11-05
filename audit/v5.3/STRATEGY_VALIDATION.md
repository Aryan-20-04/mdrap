# MDRAP v5.3 — Strategy Validation & Microstructure Report

## Overview
This document evaluates the 4 core quantitative strategies supported by MDRAP v5.3:
1. **Avellaneda–Stoikov Optimal Market Making** (`compute_avellaneda_stoikov`)
2. **Linear Inventory-Skewed Market Making** (`compute_linear_inventory_skew`)
3. **Whale Momentum Strategy** (`WhaleMomentumStrategy`)
4. **Spread Capture High-Frequency MM** (`SpreadCaptureMarketMaker`)

All evaluations were executed against the event-driven matching simulator under deterministic seed conditions (`seed=42`).

---

## 1. Strategy Mathematical Formulation & Assumptions

### 1.1 Avellaneda–Stoikov (2008)
- **Reservation Price**:
  $$r(s, q, t) = s - q \cdot \gamma \cdot \sigma^2 \cdot (T - t)$$
- **Optimal Half-Spread**:
  $$\delta = \frac{1}{2}\left[\gamma \sigma^2 (T - t) + \frac{2}{\gamma} \ln\left(1 + \frac{\gamma}{\kappa}\right)\right]$$
- **Parameters**: Risk-aversion $\gamma=0.1$, Order arrival intensity $\kappa=1.5$, Normalized horizon $T-t=1.0$, Volatility $\sigma=0.02$.
- **Behavior**: As inventory accumulates positive shares ($q > 0$), the reservation price skews downward, shading bid quotes away from the market and pulling ask quotes closer to entice buying inventory runoff.

### 1.2 Linear Inventory-Skewed MM
- **Reservation Price**:
  $$r(s, q) = s - q \cdot \lambda$$
- **Parameters**: Fixed half-spread $\delta=0.08$, Inventory penalty $\lambda=0.03$.
- **Behavior**: Simpler heuristic rule commonly utilized by institutional desks due to predictable quote offsets that do not depend on real-time continuous estimation of volatility $\sigma$.

### 1.3 Whale Momentum Strategy
- **Signal**: Detects aggregate trade volumes exceeding $5.0 \times$ prevailing 1-minute rolling average.
- **Action**: Aggresses into the order book (taker orders) in the direction of the whale flow, anticipating immediate short-term order book sweeping and price continuation.

### 1.4 Spread Capture Strategy
- **Signal**: Posts passive bids and asks at top of book during tight-spread regimes.
- **Action**: Relies strictly on maker fee rebates or narrow spread collection ($0.05$ offset), cancelling immediately when book imbalance exceeds $70\%$.

---

## 2. Empirical Results & Performance Scorecard

The following table summarizes the measured metrics from `audit/v5.3/raw/quantitative_audit.json`:

| Strategy | Capital (\$k) | Net PnL (\$) | Trades | Win Rate | Profit Factor | Sharpe | Sortino | Calmar | PSR | DSR | Max DD |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **AS_MM** | 100.0 | \$269.68 | 500 | 100.0% | 299.8 | 0.07 | 0.07 | 0.14 | 0.9896 | 0.0000 | 0.51% |
| **LINEAR_SKEW_MM** | 100.0 | \$567.93 | 467 | 100.0% | 344.2 | 0.16 | 0.16 | 0.36 | 0.9999 | 0.0002 | 0.44% |
| **WHALE_MOMENTUM** | 100.0 | \$112.50 | 166 | 60.2% | 1.45 | 0.11 | 0.10 | 0.19 | 0.9540 | 0.0000 | 0.58% |
| **SPREAD_CAPTURE** | 100.0 | \$384.20 | 999 | 100.0% | 158.4 | 0.22 | 0.22 | 0.65 | 1.0000 | 0.0015 | 0.35% |

---

## 3. Microstructure & Execution Vulnerability Analysis

1. **Inventory Runaway in Trending Markets**:
   Both AS_MM and Linear Skew MM accumulate large directional positions (up to 168 units in AS_MM) during persistent unidirectional drifts. Without an explicit hard inventory ceiling, mark-to-market risk becomes substantial.
2. **Adverse Selection Markouts**:
   When matching latency exceeds $2.0$ ms, adverse selection risk jumps from $0\%$ to $100\%$ (see `audit/v5.3/raw/benchmarks_raw.json` latency sweeps), as informed orders take advantage of stale resting limit quotes.
3. **Execution Feasibility**:
   Spread Capture requires zero-queue or sub-millisecond priority to maintain a $100\%$ win rate; under realistic exchange FIFO queue delays ($> 0.5$ ms), queue position decays and fill rates fall.
