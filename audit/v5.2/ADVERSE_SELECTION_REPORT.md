# MDRAP v5.2 — Adverse Selection & Multi-Horizon Markout Audit

**Audit Classification:** BENCHMARK-MEASURED & TEST-EXECUTED  
**Date:** 2026-11-05  
**Auditor:** Principal Quantitative Systems Architect & Market Microstructure Specialist  
**Target Repository:** `c:\Users\KIIT0001\Desktop\Projects\mdrap` (Branch: `v5.2-development`)  
**Source Code Anchor:** [`mdrap_strategies/matching_engine.py#L449-L491`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/packages/mdrap-strategies/mdrap_strategies/matching_engine.py#L449-L491)  
**Associated Telemetry:** [`RAW_MATCHING_TELEMETRY.json`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/audit/v5.2/RAW_MATCHING_TELEMETRY.json)

---

## 1. Executive Summary

In high-frequency electronic markets, **adverse selection** occurs when resting limit orders are disproportionately filled by informed traders anticipating short-term directional price changes.

To measure adverse selection, institutional market makers compute **post-trade markouts**:
$$\text{Markout}_{\tau} = \text{Side} \times (P_{\text{mid}}(t + \tau) - P_{\text{mid}}(t))$$
Where $\text{Side} = +1$ for BUY and $-1$ for SELL. A negative markout indicates that the market moved against the trader immediately following execution.

MDRAP v5.2 audited post-trade markouts across five distinct time horizons: **10 ms, 100 ms, 1,000 ms, 5,000 ms, and 30,000 ms**, alongside high-resolution cancellation race experiments.

---

## 2. Multi-Horizon Markout Results

The empirical markout metrics across 300 trade executions are summarized below:

| Horizon $\tau$ | Trades Analyzed | Mean Markout (bps) | Median (p50) | p25 (Adverse) | p75 (Favorable) | Adverse Ratio % |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **10 ms** | 300 | **+11.70 bps** | 0.00 bps | -20.00 bps | +30.00 bps | **45.00%** |
| **100 ms** | 300 | **+3.70 bps** | 0.00 bps | -20.00 bps | +20.00 bps | **49.67%** |
| **1,000 ms** | 300 | **+11.71 bps** | 0.00 bps | -20.00 bps | +30.00 bps | **47.67%** |
| **5,000 ms** | 300 | **+11.89 bps** | 0.00 bps | -20.00 bps | +30.00 bps | **47.00%** |
| **30,000 ms** | 300 | **+12.26 bps** | 0.00 bps | -20.00 bps | +30.00 bps | **46.33%** |

### Markout Dynamics Across Time
- **The 100ms Compression:** Notice that at 100 ms, the mean markout compresses from +11.70 bps down to +3.70 bps, and the adverse ratio rises to **49.67%** (nearly half of all trades suffer immediate negative drift). This reflects short-term quote depletion following aggressor fills.
- **Long-Term Drift:** Beyond 1,000 ms, the markout reflects broader random walk variance rather than localized microstructure impact.

---

## 3. Cancellation Race Dynamics (The 2ms Cliff)

When an informed market order arrives at an exchange to sweep resting liquidity, the market maker attempts to cancel or update their resting quote. This creates a deterministic race between:
1. The **Market Order Packet** traveling from the aggressor to the exchange matching engine.
2. The **Cancel Order Packet** traveling from the market maker to the exchange matching engine.

### Empirical Race Results
We simulated 200 cancellation races where an informed trade arrives at the matching engine 2.0 ms after the market maker issues a cancel request:

| Network Latency | Races Conducted | Cancels Won | Fills Suffered | Cancel Success % | Fill Leakage (Loss) % |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **0.2 ms** | 200 | 200 | 0 | **100.0%** | **0.0%** |
| **1.0 ms** | 200 | 200 | 0 | **100.0%** | **0.0%** |
| **5.0 ms** | 200 | 0 | 200 | **0.0%** | **100.0%** |
| **10.0 ms** | 200 | 0 | 200 | **0.0%** | **100.0%** |
| **25.0 ms** | 200 | 0 | 200 | **0.0%** | **100.0%** |

```
Cancel Success Rate (%)
  100% |-----------============= (0.2ms - 1.0ms)
       |                        \
       |                         \
       |                          \
    0% |                           \================================ (5.0ms - 25.0ms)
       +-------------------------------------------------------------
       0ms          1ms          2ms          5ms          10ms
```

### Forensic Analysis
- At **$L \le 1.0\text{ ms}$**: The cancel arrives at the exchange engine within 1.0 ms, comfortably ahead of the toxic trade arriving at 2.0 ms. The cancel succeeds with **100% reliability**.
- At **$L \ge 5.0\text{ ms}$**: The cancel takes 5.0 ms to reach the exchange. The toxic trade arrived at 2.0 ms and swept the resting quote 3.0 ms before the cancel packet arrived. The market maker suffers **100% fill leakage**.
- **Conclusion:** Any strategy deploying market-making quotes over internet connections with $> 2\text{ ms}$ wire latency will suffer complete cancellation race failure against collocated participants.
