# MDRAP Phase 5 — Operational Drift Detection and Clock Skew Monitoring

## 1. Executive Summary & Context
In high-frequency and institutional market data processing, operational drift represents subtle, gradual shifts in system timing, feed synchronization, or statistical market properties that do not trigger hard errors but degrade downstream trading precision. This document defines the detection mechanisms, thresholds, and alerts for **Clock Synchronization Drift**, **Feed Arrival Skew Drift**, and **Microstructure Volatility Drift**.

---

## 2. Clock Synchronization & Timestamp Drift (PTP / NTP)

### 2.1 The Invariant
Every incoming market event carries an exchange timestamp ($T_{\text{ex}}$) and is stamped with local ingress hardware receive time ($T_{\text{recv}}$). By definition of causality in physical network propagation:
$$T_{\text{recv}} > T_{\text{ex}}$$
If local host clock drifts backwards or is improperly synchronized with atomic UTC (via PTP IEEE 1588 or Chrony NTP), the pipeline will record false negative latency or apparent causality violations.

### 2.2 Drift Monitor & Guard
- **Check**: Gateway checks $\Delta_{\text{clock}} = T_{\text{recv}} - T_{\text{ex}}$.
- **Anomaly Boundary**:
  - If $\Delta_{\text{clock}} < -100\text{ \mu s}$: Event flagged with `Reason.CLOCK_DRIFT_NEGATIVE`. Status set to `SUSPICIOUS`.
  - If $\Delta_{\text{clock}} > 2,000,000\text{ \mu s}$ (2.0 seconds): Event flagged with `Reason.STALE_TIMESTAMP`. Status set to `SUSPICIOUS`.
- **System Action**: Prometheus metric `mdrap_clock_drift_seconds` updated. PTP sync daemon status scraped via `/health/ready`.

---

## 3. Feed Arrival Skew Drift (Multi-Source Latency Divergence)

### 3.1 The Scenario
When monitoring redundant feeds (e.g., NASDAQ direct ITCH vs SIP / consolidated feed for the same symbol), feed A typically arrives 1.2 ms ahead of feed B. If network routing degrades or an ISP peer drops, feed A's arrival time can gradually drift behind feed B without dropping packets.

### 3.2 Detection Algorithm
The Reconciler (`src/reconciliation.py`) tracks the rolling EWMA arrival delta between competing feeds:
$$\delta_{t} = \alpha (T_{\text{recv}, B} - T_{\text{recv}, A}) + (1 - \alpha) \delta_{t-1}$$
- If $\delta_t$ deviates by more than $3\sigma$ from the historical 1-hour moving baseline, alert `MDRAPFeedArrivalSkewWarning` is emitted.
- The reconciler dynamically adjusts the cross-feed matching window to prevent prematurely selecting a slower feed.

---

## 4. Statistical Volatility Drift (Welford Anomaly Drift)

### 4.1 Rolling Moment Tracking
MDRAP tracks the rolling mean $\mu$ and sample variance $\sigma^2$ of tick-to-tick price changes per instrument in $O(1)$ time via Welford's algorithm:
$$M_{2, k} = M_{2, k-1} + (x_k - \mu_{k-1})(x_k - \mu_k)$$
$$\sigma_k = \sqrt{\frac{M_{2, k}}{k}}$$

### 4.2 Non-Stationarity and Regime Shift Adaptation
Financial markets experience regime shifts (e.g., sudden Federal Reserve rate decisions or earnings releases) where volatility structurally expands. If static sigma thresholds are applied, genuine market moves are falsely rejected as data errors:
- **Exponential Half-Life**: MDRAP decays historical sample weights with a half-life of 300 seconds during active trading sessions.
- **Rule Hierarchy Invariant**: Quality status has strict priority: `INVALID > SUSPICIOUS > VALID`. Real market volatility jumps are classified as `SUSPICIOUS` (retained for trading, with provenance tag), never downgraded or silently dropped.
