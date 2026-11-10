# MDRAP Market Data Quality & Cross-Venue Intelligence

**Document Version**: 2.0  
**Responsible Agent**: Agent 4 (Market Data Quality & Cross-Venue Intelligence)  
**Status**: VERIFIED IMPLEMENTATION  

---

## 1. Architectural Mission

The Market Data Intelligence engine is the primary analytical differentiator of MDRAP. Its mission is to answer:
*"Is this incoming tick trustworthy, is the market behaving abnormally, or has our feed connector encountered a data corruption error?"*

Crucially, MDRAP separates **market anomalies** (genuine market volatility, temporary price divergence, wide spreads) from **data quality defects** (corrupted payloads, sequence gaps, backpressure drops).

---

## 2. Core Anomaly Detection Taxonomy

MDRAP evaluates every incoming event against institutional quality criteria with strict status hierarchy:
$$\text{INVALID (Quarantine)} > \text{SUSPICIOUS} > \text{VALID}$$

| Anomaly Category | Diagnostic Code | Classification | Mathematical / Operational Rule | Operational Significance |
| :--- | :--- | :--- | :--- | :--- |
| **Schema / Structural** | `SCHEMA_VIOLATION` | **INVALID** | Event missing mandatory keys or unparseable JSON payload | Ingestion failure; immediate quarantine |
| **Negative Price/Size** | `NEGATIVE_PRICE` | **INVALID** | `price < 0` or `bid_price < 0` (outside negative energy contracts) | Data corruption; quarantined |
| **Monotonic Sequence Gap** | `SEQUENCE_GAP` | **SUSPICIOUS** | `seq > prev_seq + 1` where monotonic sequence exposed | Feed handler or network dropped $N$ frames |
| **Duplicate Event** | `DUPLICATE_SEQUENCE` | **SUSPICIOUS** | Event sequence number identical to recently processed frame | Replay or retransmission storm |
| **Out-of-Order Delivery**| `OUT_OF_ORDER` | **SUSPICIOUS** | `exchange_ts < prev_exchange_ts` | Network packet reordering or multi-channel race |
| **Timestamp Implausible** | `TS_IMPLAUSIBLE` | **SUSPICIOUS** | `exchange_ts > receive_ts + max_future_skew` ($>1.0\text{s}$) | Source exchange server clock drift |
| **Stale Feed** | `STALE_FEED` | **SUSPICIOUS** | `receive_ts - exchange_ts > 50\text{ms}` | Network latency congestion or stalled publisher |
| **Crossed Internal Book**| `CROSSED_QUOTE` | **SUSPICIOUS** | Single venue $Bid > Ask$ | Venue matching engine glitch |
| **Price Corridor Spike** | `PRICE_SPIKE_SIGMA`| **SUSPICIOUS** | $|P - \mu| > 6\sigma$ within rolling $N=50$ tick window | Flash crash, fat finger, or rogue tick |

---

## 3. Cross-Venue Intelligence: Distinguishing Disagreement from Arbitrage

A frequent failure of naive market surveillance tools is declaring every price difference across exchanges as "arbitrage" or "bad data". MDRAP implements nuanced cross-venue intelligence:

### 3.1 Normalization Primitives
Before comparing prices across venues:
1. **Denomination Alignment**: Confirm identical base and quote currency (e.g. `BTC/USDT` on Binance vs. `BTC/USD` on Kraken are distinct pairs with distinct stablecoin risk).
2. **Quote Age Discounting**: If Venue A's quote is 350 ms old while Venue B's quote was received 10 ms ago, the price divergence is classified as `STALE_LAG`, not an executable crossed market.
3. **Fee & Friction Hurdles**: A cross-venue crossed book ($Bid_A > Ask_B$) is only flagged as `EXECUTABLE_ARBITRAGE` if:
   $$Bid_A - Ask_B > \text{TakerFee}_A + \text{TakerFee}_B + \text{SlippageAllowance}$$
   Otherwise, it is classified as `NON_EXECUTABLE_MICROSTRUCTURE_DISAGREEMENT`.

### 3.2 Explainable Findings vs. Opaque Scores
MDRAP replaces single opaque scores with explainable diagnostic manifests:
```json
{
  "finding": "CROSS_VENUE_DIVERGENCE",
  "instrument": "BTC/USD",
  "venues": ["BYBIT", "BINANCE"],
  "price_delta": 18.09,
  "delta_bps": 2.47,
  "classification": "VALID_MARKET_DISAGREEMENT",
  "explanation": "Bybit bid leads Binance by $18.09; Binance quote received within 5ms; divergence within normal cross-exchange taker fee band (10 bps)."
}
```
