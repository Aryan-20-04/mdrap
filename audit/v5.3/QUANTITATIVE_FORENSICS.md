# MDRAP v5.3 — Quantitative Forensics & Mathematical Audit

## Executive Summary
This document records the adversarial mathematical investigation into the quantitative strategies, return distributions, Sharpe ratios, and cross-venue arbitrage claims evaluated in MDRAP v5.2.

---

## 1. Mathematical Breakdown of Sharpe Ratio Distortion

### 1.1 The High-Frequency Annualization Fallacy
In v5.1.1, strategies reported Sharpe ratios exceeding **44.32**.
Forensic source inspection revealed that trade-level returns (calculated per fill event) were annualized using:
$$\text{Annualization Factor} = \sqrt{252 \times 23,400} \approx 2,428.3$$

This formula assumes that each trade represents an independent 1-second holding period and that trading continues non-stop across all 23,400 trading seconds per day. In reality:
- Trades were clustered within milliseconds during high-volatility bursts.
- Holding periods were non-uniform.
- Autocorrelation of intraday returns violates the i.i.d. assumption required by the standard Sharpe metric.

### 1.2 Honest Mark-to-Market Sharpe Ratio
Under honest daily-equivalent marking ($\sqrt{252}$ scaling on aggregated discrete periodic intervals), the observed Sharpe ratio collapses to:
- **Avellaneda–Stoikov Market Maker**: $0.07$ (confidence interval $[0.01, 0.14]$).
- **Linear Inventory Skew Market Maker**: $0.16$ (confidence interval $[0.08, 0.24]$).
- **Whale Momentum**: $0.11$.
- **Spread Capture**: $0.22$.

### 1.3 Probabilistic & Deflated Sharpe Ratios (Bailey & López de Prado, 2012 / 2014)
To account for non-normality (skewness and excess kurtosis) and multiple hypothesis testing (data snooping over $K=10$ trials):
- **Return Kurtosis**: $13.72$ (heavy fat tails).
- **Probabilistic Sharpe Ratio (PSR)**: $0.9896$ against zero benchmark.
- **Deflated Sharpe Ratio (DSR)**: $0.0000$ when adjusted for selection bias over multiple backtest iterations.

**Conclusion**: When subjected to institutional statistical rigor, the claimed historical alpha does not represent genuine out-of-sample risk-adjusted outperformance; it reflects selection bias and high kurtosis.

---

## 2. Forensic Resolution of Strategy Conflation

### 2.1 The Root Cause in v5.2
In `benchmarks/run_v52_quantitative_audit.py` line 189:
```python
elif strategy_type in ("AS_MM", "INVENTORY_SKEWED_MM"):
    quotes = compute_avellaneda_stoikov(mid, inv, vol)
```
Both strategies executed identical code, generating identical quotes and bit-identical PnL (\$1,562.68 across 1,599 trades).

### 2.2 Remediation in v5.3
Implemented `compute_linear_inventory_skew()` in `packages/mdrap-strategies/mdrap_strategies/microstructure.py`:
$$r(s, q) = s - q \cdot \lambda$$
where $\lambda$ is the linear inventory penalty parameter ($0.03$).

### 2.3 Measured Performance Comparison in v5.3
Telemetry recorded in `audit/v5.3/raw/quantitative_audit.json`:

| Metric | Avellaneda–Stoikov (AS_MM) | Linear Skew (LINEAR_SKEW_MM) | Variance |
| :--- | :--- | :--- | :--- |
| **Total Trades** | 500 | 467 | -33 trades |
| **Gross PnL** | \$769.68 | \$1,034.93 | +\$265.25 |
| **Fees Paid** | \$500.00 | \$467.00 | -\$33.00 |
| **Net PnL** | \$269.68 | \$567.93 | +\$298.25 |
| **Peak Inventory** | 168.0 units | 155.0 units | -13.0 units |
| **Max Drawdown** | 0.51% (\$507.50) | 0.44% (\$440.00) | -0.07% |
| **Annualized Sharpe** | 0.07 | 0.16 | +0.09 |

---

## 3. Cross-Venue Triangular Arbitrage Fallacy

### 3.1 Forensic Finding
In v5.2, an apparent \$76.09 spread was observed between Coinbase BTC-USD (\$64,200.00) and Binance BTC-USDT (\$64,276.09).
This was erroneously claimed as executable arbitrage.

### 3.2 The Reality of Basis and Taker Drag
1. **USDT/USD Basis Discount**: During the observation window, USDT traded at $\$0.9990$ per USD.
   $$\text{Binance USD Equivalent} = \$64,276.09 \times 0.9990 = \$64,211.81$$
   $$\text{True Gross Spread} = \$64,211.81 - \$64,200.00 = \$11.81$$
2. **Bilateral Taker Fees**:
   - Coinbase Taker Fee ($4.0$ bps): $\$25.68$
   - Binance Taker Fee ($7.0$ bps): $\$44.95$
   - Minimum Slippage Buffer ($1.0$ bps): $\$6.42$
   - **Total Friction**: $\$77.05$
3. **Net Realized Arbitrage PnL**:
   $$\text{Net PnL} = \$11.81 - \$77.05 = -\$65.24 \quad (-10.16 \text{ bps loss})$$

### 3.3 The Solution: `DenominationAwareValuator`
Implemented in `packages/mdrap-strategies/mdrap_strategies/fx_basis.py`. The valuator normalizes all quotes across fiat and stablecoin denominations, checks bilateral fee schedules, and rejects unprofitable illusions before order dispatch.
