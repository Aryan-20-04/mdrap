# MDRAP Data Quality Rules Specification

Based on the *Market Data Reliability & Acceleration Platform Reference (Section 6.4, 7, 11, 26)*.

## 1. Classification Taxonomy

The system defines three strict tiers of data quality with a monotonic escalation rule:
- **`VALID` (Priority 0)**: Conforms to all structural and statistical constraints.
- **`SUSPICIOUS` (Priority 1)**: Statistically anomalous or delayed, but potentially real market phenomena. Kept, flagged, explainable, and forwarded to reconciliation.
- **`INVALID` (Priority 2)**: Structurally broken, unparseable, crossed, or duplicated. Quarantined; excluded from canonical output.

> **Principle:** `_mark()` enforces priority: `INVALID` can never be demoted to `SUSPICIOUS` or `VALID`.

---

## 2. Rule Catalogue

| Rule Code | Reason Enum | Trigger Condition | Assigned Status |
|---|---|---|---|
| **R1: Schema Validation** | `SCHEMA_VIOLATION` | Missing required fields, non-finite values (NaN/Inf), negative/zero prices | `INVALID` |
| **R2: Deduplication** | `DUPLICATE` | Sequenced: duplicate in 64-slot sequence window (`INVALID`). Unsequenced: duplicate payload seen in 2-generation table (`SUSPICIOUS` default, configurable via `unseq_dup_status`) | `INVALID` / `SUSPICIOUS` |
| **R3: Crossed Book** | `CROSSED_QUOTE` | Best Bid >= Best Ask (`bid_price >= ask_price`) | `INVALID` |
| **R4: Sequence Gap** | `SEQUENCE_GAP` | `event.sequence_number > last_seq + 1` for same `(source, instrument)` | `SUSPICIOUS` |
| **R5: Out of Order** | `OUT_OF_ORDER` | `sequence_number < last_seq` (within 64 slots) or `exchange_timestamp < last_ts` | `SUSPICIOUS` |
| **R6: Staleness** | `STALE` | `receive_ts - exchange_ts > staleness_threshold_s` (default 50ms) | `SUSPICIOUS` |
| **R7: Price Anomaly** | `PRICE_ANOMALY` | `abs(price - rolling_mean) > k * sigma_eff` where $k=6.0$ ($6\sigma$), $\sigma_{\text{eff}} = \max(\sigma, \text{floor}\cdot |\text{mean}|)$ | `SUSPICIOUS` |
| **R8: Cross-Feed Disagreement** | `CROSS_FEED_DISAGREEMENT` | Divergence between multiple sources during reconciliation | `SUSPICIOUS` |
| **R9: Implausible Timestamp** | `TS_IMPLAUSIBLE` | `exchange_timestamp > receive_timestamp + max_future_skew_s` (default 1.0s) | `SUSPICIOUS` |

---

## 3. Statistical Baseline & Welford's Algorithm Implementation

Price anomaly detection uses replace-update Welford online variance algorithm with fixed-window eviction:
- **Clean-only baseline**: Only valid events with non-anomalous prices are folded into the baseline. Anomalous and invalid events are never folded.
- **Warm-up boundary**: During the first 20 samples (`price_min_samples = 20`), a coarse $\pm 10\%$ sanity check applies instead of the $\sigma$ test to prevent false positives from tick quantization.
- **Relative $\sigma$-floor**: $\sigma_{\text{eff}} = \max(\sigma, 2\times 10^{-4} \cdot |\text{mean}|)$ protects against false positives in low-volatility tight spreads.
- **Regime shift auto-recovery**: If 8 consecutive events (`price_reseed_after = 8`) occur at a new consistent price level, the baseline automatically re-seeds to the new price, allowing subsequent ticks to return to `VALID`.
