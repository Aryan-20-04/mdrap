# MDRAP v5.3 — Microstructure Latency & Adverse Selection Sweeps

## Executive Summary
This document presents empirical measurements of order execution performance, fill rates, and adverse selection markouts as a function of network and matching engine latency.

---

## 1. Latency Sweep Empirical Results

Sweeps conducted from $0.1\text{ ms}$ to $20.0\text{ ms}$ simulated exchange delay (telemetry recorded in `audit/v5.3/raw/quantitative_audit.json` under `latency_sweeps`):

| Latency (ms) | Orders Submitted | Orders Filled | Fill Rate (%) | Adverse Selection Events | Adverse Risk (%) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **0.1 ms** | 100 | 100 | 100.0% | 0 | 0.0% |
| **0.5 ms** | 100 | 100 | 100.0% | 0 | 0.0% |
| **1.0 ms** | 100 | 100 | 100.0% | 0 | 0.0% |
| **2.0 ms** | 100 | 100 | 100.0% | 0 | 0.0% |
| **5.0 ms** | 100 | 100 | 100.0% | 100 | 100.0% |
| **10.0 ms** | 100 | 100 | 100.0% | 100 | 100.0% |
| **20.0 ms** | 100 | 100 | 100.0% | 100 | 100.0% |

---

## 2. Microstructure Insights & Latency Thresholds

### 2.1 The 2.0ms Critical Latency Boundary
- At latencies $\le 2.0\text{ ms}$, passive limit orders reach the exchange order book ahead of market price adjustments. Fills occur at the intended quote price with zero adverse selection.
- At latencies $> 2.0\text{ ms}$ (e.g. $5.0\text{ ms}$ and above), the simulated market moves before the order arrives. Resting limit orders are picked off by informed aggressors, resulting in $100\%$ adverse markout exposure.

### 2.2 Institutional Recommendations
1. **Co-location / Cross-Connect**:
   Trading strategies requiring passive spread capture or Avellaneda-Stoikov market making must achieve wire latency $< 1.0\text{ ms}$ to remain viable.
2. **Dynamic Queue Cancellation**:
   When latency spikes above $2.0\text{ ms}$, passive limit orders should be cancelled immediately via emergency cancel sweeps (`cancel_all`).
