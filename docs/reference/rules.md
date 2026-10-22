# Quality Rules Reference Catalog

MDRAP evaluates 15 deterministic quality checks on every incoming market event. Rules are implemented in both native C (`src/fastpath.c`) and pure Python (`src/mdrap/quality.py`) with bit-for-bit parity proven by differential fuzz testing.

---

## 1. Quality Rule Summary Table

| Bit | Hex Mask | Rule Name | Resulting Status | Evaluation Scope |
| :---: | :---: | :--- | :--- | :--- |
| **0** | `0x0001` | `SCHEMA_VIOLATION` | **INVALID** | Structural fields, missing keys, NaN/Inf floats |
| **1** | `0x0002` | `DUPLICATE` | **INVALID** | Monotonic sequence deduplication within session |
| **2** | `0x0004` | `SEQUENCE_GAP` | **SUSPICIOUS** | Packet loss detection ($\text{seq}_i > \text{seq}_{i-1} + 1$) |
| **3** | `0x0008` | `OUT_OF_ORDER` | **SUSPICIOUS** | Inverted sequence ($\text{seq}_i < \text{seq}_{i-1}$) or non-monotonic timestamps |
| **4** | `0x0010` | `STALE` | **SUSPICIOUS** | $t_{\text{recv}} - t_{\text{exch}} > \text{threshold}$ |
| **5** | `0x0020` | `PRICE_ANOMALY` | **SUSPICIOUS** | $|P_i - \bar{P}| > k \times \sigma$ (Welford variance) |
| **6** | `0x0040` | `CROSSED_QUOTE` | **INVALID** | $P_{\text{bid}} > P_{\text{ask}}$ |
| **7** | `0x0080` | `CROSS_FEED_DISAGREEMENT` | **SUSPICIOUS** | Multi-venue consensus divergence exceeds tolerance |
| **8** | `0x0100` | `MALFORMED` | **INVALID** | Unparseable wire bytes or protocol corruption |
| **9** | `0x0200` | `CIRCUIT_FILTER_BREACH` | **INVALID** | $|P_i - P_{\text{close}}| / P_{\text{close}} > \text{circuit\_pct}$ (e.g. $\pm 10\%$) |
| **10** | `0x0400` | `VOLATILITY_INTERRUPTION` | **SUSPICIOUS** | Dynamic price corridor breach (e.g. $\pm 5\%$) |
| **11** | `0x0800` | `SPECIAL_QUOTE_INDICATION` | **SUSPICIOUS** | Exchange quote indication (e.g. TSE Tokuhai $\pm 8\%$) |
| **12** | `0x1000` | `TS_IMPLAUSIBLE` | **INVALID** | $t_{\text{exch}} - t_{\text{recv}} > \text{clock\_skew\_tolerance}$ (future clock drift) |
| **13** | `0x2000` | `RATE_LIMITED` | **SUSPICIOUS** | Event frequency breaches source rate limit |
| **14** | `0x4000` | `SECURITY_REJECT` | **INVALID** | Authentication failure, sanitizer rejection, or HMAC mismatch |

---

## 2. Rule Specifications & Formulas

### Bit 0: `SCHEMA_VIOLATION`
- **Condition**: Event payload missing required fields (`instrument`, `event_type`, `price`/`quantity` for trades, or `bid`/`ask` for quotes), or numerical values contain `NaN` or `Inf`.
- **Handling**: Quarantined immediately. Event is excluded from order book and state projection.

### Bit 1: `DUPLICATE`
- **Condition**: An incoming event carries a sequence number or `event_id` already processed within the bounded LRU deduplication window.
- **Handling**: Quarantined as `INVALID`.

### Bit 2: `SEQUENCE_GAP`
- **Condition**: Sequence increment exceeds 1 ($\Delta \text{seq} > 1$) on a sequenced feed.
- **Handling**: Flagged as `SUSPICIOUS`. Gap manager registers missing sequence range for potential recovery/replay.

### Bit 4: `STALE`
- **Condition**: $(t_{\text{receive}} - t_{\text{exchange}}) > T_{\text{staleness}}$.
- **Default**: $0.05\text{ s}$ ($50\text{ ms}$). Configurable per venue and instrument in `mdrap.toml`.
- **Handling**: Flagged as `SUSPICIOUS`. Downstream strategies can decide whether to consume or drop.

### Bit 5: `PRICE_ANOMALY`
- **Condition**: Evaluated using Welford's algorithm over rolling window $N$:
  $$\mu_k = \mu_{k-1} + \frac{x_k - \mu_{k-1}}{k}$$
  $$M_{2,k} = M_{2,k-1} + (x_k - \mu_{k-1})(x_k - \mu_k)$$
  $$\sigma = \sqrt{\frac{M_{2,k}}{k - 1}}$$
  Triggered when $|x_k - \mu| > k_{\text{stddev}} \times \sigma$.
- **Handling**: Flagged as `SUSPICIOUS` (market volatility events are not automatically corrupted data).

### Bit 6: `CROSSED_QUOTE`
- **Condition**: On a 2-sided quote, best bid price is strictly greater than best ask price ($P_{\text{bid}} > P_{\text{ask}}$).
- **Handling**: Quarantined as `INVALID`.

### Bit 9: `CIRCUIT_FILTER_BREACH`
- **Condition**: Price deviation from previous session reference price exceeds exchange daily circuit limit:
  $$\frac{|P - P_{\text{ref}}|}{P_{\text{ref}}} > \text{limit\_pct}$$
- **Handling**: Quarantined as `INVALID`.
