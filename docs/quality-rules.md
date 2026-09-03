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
| **R1: Schema Validation** | `SCHEMA_VIOLATION` | Missing required fields, invalid types, unparseable values | `INVALID` |
| **R2: Deduplication** | `DUPLICATE` | Identical `dedup_key()` observed in recent LRU window | `INVALID` |
| **R3: Crossed Book** | `CROSSED_QUOTE` | Best Bid > Best Ask (`bid_price > ask_price`) | `INVALID` |
| **R4: Sequence Gap** | `SEQUENCE_GAP` | `event.sequence_number > last_seq + 1` for same `(source, instrument)` | `SUSPICIOUS` |
| **R5: Out of Order** | `OUT_OF_ORDER` | `exchange_timestamp < last_ts` for same `(source, instrument)` | `SUSPICIOUS` |
| **R6: Staleness** | `STALE` | `receive_ts - exchange_ts > staleness_threshold_s` (default 50ms) | `SUSPICIOUS` |
| **R7: Price Anomaly** | `PRICE_ANOMALY` | `abs(price - rolling_mean) > k * rolling_stddev` (Welford's z-score) | `SUSPICIOUS` |
| **R8: Cross-Feed Disagreement** | `CROSS_FEED_DISAGREEMENT` | Divergence between multiple sources during reconciliation | `SUSPICIOUS` |

---

## 3. Welford's Algorithm Implementation

Price anomaly detection uses Welford's one-pass online algorithm with fixed-window eviction to guarantee numerical stability:
- Mean and variance are computed using deviation sums rather than sum-of-squares to eliminate floating-point cancellation.
- The point under test is tested against the **prior** baseline window before being folded in.
