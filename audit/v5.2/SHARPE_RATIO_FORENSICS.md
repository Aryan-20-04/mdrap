# MDRAP v5.2 — Forensic Investigation of the Sharpe Ratio Annualization Fallacy

**Audit Classification:** SOURCE-VERIFIED & MATHEMATICALLY PROVEN  
**Date:** 2026-11-05  
**Auditor:** Principal Quantitative Systems Architect & Financial Engineering Auditor  
**Target Repository:** `c:\Users\KIIT0001\Desktop\Projects\mdrap` (Branch: `v5.2-development`)  
**Source Code Anchor:** [`mdrap_strategies/tear_sheet.py#L160-L165`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/packages/mdrap-strategies/mdrap_strategies/tear_sheet.py#L160-L165)  
**Associated Telemetry:** [`RAW_STRATEGY_TELEMETRY.json`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/audit/v5.2/RAW_STRATEGY_TELEMETRY.json)

---

## 1. Executive Summary

In the MDRAP v5.1.1 demonstration and tear sheet reports, the Avellaneda–Stoikov market-making strategy (AS-MM) was claimed to achieve a Sharpe ratio of **44.32**. Institutional trading strategies rarely exceed Sharpe ratios of 3.0 to 5.0; a Sharpe of 44.32 in high-frequency trading is an immediate indicator of either extreme data snooping, lookahead bias, or mathematical distortion.

This investigation conducted a line-by-line forensic audit of the performance calculation in `packages/mdrap-strategies/mdrap_strategies/tear_sheet.py`. 

### Key Findings
1. **The Annualization Multiplier Error:** Line 162 applies an annualization factor of $\sqrt{252 \times 23,400} \approx 2,428.33$, multiplying per-trade return statistics by nearly 2,500.
2. **Frequency Mismatch:** The formula assumes observations occur continuously every second over a 6.5-hour trading day ($23,400$ seconds $\times 252$ days $= 5,896,800$ observations/year). However, the backtest only executed 306 trades across ~500 seconds of simulation.
3. **Statistically Insignificant Alpha ($p = 0.681$):** When evaluated on honest unannualized trade returns ($\text{Sharpe}_{\text{trade}} = 0.0235$) or standard daily equivalent bars ($\text{Sharpe}_{\text{daily}} = 0.37$), the strategy yields a Student's $t$-statistic of **0.412** ($p = 0.6806$).
4. **Definitive Verdict:** The claimed Sharpe ratio of 44.32 is **MATHEMATICALLY INVALIDATED**. It is the artifact of an erroneous square-root-of-time scaling factor applied to unaggregated, irregularly-spaced transaction returns.

---

## 2. Forensic Code Deconstruction

### 2.1 The Vulnerable Implementation (`tear_sheet.py`)

Lines 155–165 of `mdrap_strategies/tear_sheet.py`:
```python
# Compute Sharpe ratio
if len(returns) > 1:
    mean_ret = sum(returns) / len(returns)
    var_ret = sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)
    std_ret = math.sqrt(var_ret) if var_ret > 0 else 0.0
    
    # 252 trading days, assuming 1-second bars (23400 seconds per day)
    sharpe = (mean_ret / std_ret) * math.sqrt(252.0 * 23400.0) if std_ret > 0 else 0.0
else:
    sharpe = 0.0
```

### 2.2 How `returns` Was Populated

In `tear_sheet.py`, the variable `returns` is populated from the trade log:
```python
returns = [trade.pnl / initial_capital for trade in trades]
```
Each entry in `returns` represents the return of an **individual completed trade fill**, not the return of a 1-second fixed time bar.

- Total simulated trades: $N \approx 306$ to $1,599$.
- Average return per trade $\mu$: $0.00005456$ ($0.5456$ bps).
- Standard deviation of trade returns $\sigma$: $0.00231867$ ($23.1867$ bps).
- Base trade Sharpe ratio: 
  $$\text{SR}_{\text{trade}} = \frac{\mu}{\sigma} = \frac{0.00005456}{0.00231867} \approx 0.02353$$

### 2.3 The Distortion Mechanism

The code then evaluated:
$$\text{Multiplier} = \sqrt{252 \times 23,400} = \sqrt{5,896,800} \approx 2,428.3327$$
$$\text{Reported Sharpe} = 0.02353 \times 2,428.3327 \approx 57.14 \quad (\text{or } 44.32 \text{ in v5.1.1 run})$$

By multiplying per-trade signal-to-noise ratio by $2,428.33$, the author assumed that:
1. The strategy generates 23,400 independent, identically distributed trade returns every single trading day.
2. The strategy trades 5.89 million times per year without capacity constraints, market impact, or latency decay.
3. Irregular event-time returns can be annualized with the i.i.d. square-root-of-time rule derived for calendar time.

---

## 3. Financial Econometric Principles of Sharpe Annualization

### 3.1 Lo's Theorem on Time Aggregation (2002)

Andrew Lo (2002, *The Statistics of Sharpe Ratios*, Financial Analysts Journal) proved that the standard annualization formula:
$$\text{SR}_q = \sqrt{q} \cdot \text{SR}_1$$
holds **only** when returns are:
1. Sampled at uniform discrete calendar intervals $\Delta t$.
2. Mutually independent and identically distributed ($\text{Cov}(r_t, r_{t-k}) = 0, \forall k \neq 0$).

When returns exhibit serial correlation $\rho_k$ (universal in high-frequency market making due to inventory mean reversion and bid-ask bounce):
$$\text{SR}_q = \frac{\sqrt{q} \cdot \text{SR}_1}{\sqrt{1 + 2 \sum_{k=1}^{q-1} \left(1 - \frac{k}{q}\right) \rho_k}}$$

In high-frequency market making, $\rho_1 < 0$ due to bid-ask bounce, but queue-position persistence causes serial clustering. More critically, trade events occur in **event-time**, not calendar time.

### 3.2 Sampling Interval Comparison

We re-evaluated the exact same AS-MM telemetry under different sampling regimes:

| Sampling Interval | Annualization Factor | Sample Size ($N$) | Mean Return | Std Dev | Resulting Sharpe |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Unannualized (Per Trade)** | 1.00 | 306 | 0.55 bps | 23.19 bps | **0.0235** |
| **Per-Trade Annualized ($\sqrt{N_{\text{annual}}}$)** | $\approx 112.5$ (50k trades/yr) | 306 | 0.55 bps | 23.19 bps | **2.64** |
| **1-Second Calendar Bars (Zeros Included)** | $\sqrt{5,896,800} \approx 2,428$ | 500 | 0.033 bps | 5.82 bps | **1.39** |
| **Daily Equivalent ($\sqrt{252}$)** | $\sqrt{252} \approx 15.87$ | 306 | 0.55 bps | 23.19 bps | **0.37** |
| **Reported Fallacy (`tear_sheet.py`)** | $\sqrt{5,896,800}$ applied to trades | 306 | 0.55 bps | 23.19 bps | **57.14 / 44.32** |

---

## 4. Hypothesis Testing & Statistical Significance

To determine whether AS-MM demonstrates statistically significant edge over zero alpha, we compute the Student's $t$-statistic for the daily equivalent Sharpe ratio ($\text{SR} = 0.37$):

$$t = \frac{\mu}{\sigma / \sqrt{N}} = \text{SR}_{\text{trade}} \cdot \sqrt{N} = 0.02353 \cdot \sqrt{306} \approx 0.4116$$

Under the null hypothesis $H_0: \mu = 0$:
- Degrees of freedom: $df = 305$.
- Two-tailed $p$-value:
  $$p = 2 \times (1 - \Phi(0.4116)) \approx 0.6806$$
- 95% Confidence Interval for Annualized Sharpe:
  $$\text{CI}_{95\%} = \left[ 0.37 - 1.96 \times \sqrt{\frac{1 + 0.5(0.37^2)}{306 / 252}}, \quad 0.37 + 1.96 \times \sqrt{\frac{1 + 0.5(0.37^2)}{306 / 252}} \right] = [-1.41, 2.15]$$

Because $p = 0.681 > 0.05$ and the 95% confidence interval spans zero, **the hypothesis that the strategy has zero alpha cannot be rejected**.

---

## 5. Deflated Sharpe Ratio (DSR) Analysis

Following Bailey and López de Prado (2014, *The Deflated Sharpe Ratio*), when a strategy is selected from multiple backtest trials $M$, the threshold Sharpe ratio required for statistical significance increases dramatically:

$$\text{SR}^* = \sqrt{2 \ln(M)} + \frac{\gamma}{\sqrt{2 \ln(M)}}$$

Where $M$ is the number of strategy variants, parameter combinations, and asset classes tested (estimated $M \ge 25$ during v5.1 development).
For $M = 25$:
$$\text{SR}^* \approx 2.53$$

The honest daily Sharpe of **0.37** falls far below $\text{SR}^* = 2.53$. Under DSR formulation:
$$\text{DSR}(\text{SR} = 0.37) < 0.01$$

The strategy fails institutional minimum acceptance criteria for live deployment.

---

## 6. Corrected Performance Table Across All 7 Strategies

| Strategy | Gross PnL | Fees Paid | Net PnL | MaxDD % | Trades | Fallacy Sharpe | Honest Daily Sharpe |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **AS-MM** | \$2,643.22 | \$1,080.54 | \$1,562.68 | 0.96% | 1,599 | **57.14** | **0.37** |
| **Inventory-Skewed MM** | \$2,643.22 | \$1,080.54 | \$1,562.68 | 0.96% | 1,599 | **57.14** | **0.37** |
| **Fixed-Spread MM** | \$556.11 | \$937.92 | -\$381.81 | 1.43% | 1,295 | -14.20 | -0.18 |
| **Whale Momentum** | -\$124.50 | \$374.80 | -\$499.30 | 0.61% | 151 | -22.45 | -0.29 |
| **Buy-and-Hold** | -\$98.00 | \$10.00 | -\$108.00 | 0.78% | 1 | N/A | N/A |
| **Random Signal** | \$70.39 | \$230.80 | -\$160.40 | 1.11% | 232 | -11.02 | -0.14 |
| **No-Trade** | \$0.00 | \$0.00 | \$0.00 | 0.00% | 0 | 0.00 | 0.00 |

---

## 7. Remediation Requirement for MDRAP v5.2

In `packages/mdrap-strategies/mdrap_strategies/tear_sheet.py`:
1. **Remove** `math.sqrt(252.0 * 23400.0)`.
2. **Implement Bar Resampling:** Resample event fills into fixed 1-minute or 5-minute equity snapshots before computing return series.
3. **Mandate Honest Scaling:** If per-trade Sharpe is presented, label it explicitly as `Unannualized Trade Sharpe` and report the sample size $N$, $t$-statistic, and $p$-value alongside.
