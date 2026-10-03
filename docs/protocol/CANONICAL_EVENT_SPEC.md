# MDRAP Canonical Event Specification (v2.5)

**Specification Status**: Authoritative Protocol Standard  
**Document**: `docs/protocol/CANONICAL_EVENT_SPEC.md`  
**Target Version**: v2.5.0+  

---

## 1. Canonical Event Model

A `CanonicalEvent` is the immutable, normalized market-data representation produced by the MDRAP normalization layer. Every event represents a point-in-time market occurrence with complete lineage back to its raw wire ingress frame.

### 1.1 Canonical Fields Definition

| Field Name | Type | Presence | Semantic Definition |
|---|---|---|---|
| `event_id` | `str` | **Required** | Globally unique, monotonic or deterministic event identifier (`evt-{seq}`). |
| `instrument_id` | `str` | **Required** | Canonical instrument identifier (e.g. `AAPL`, `BTC/USDT`, `AAPL260116C00150000`). **Never truncated**. |
| `symbol` | `str` | **Required** | Venue-native ticker symbol. Metadata only; never used as the primary identity key. |
| `venue` | `str` | **Required** | Venue / exchange identifier (e.g. `NASDAQ`, `BINANCE`, `CME`). |
| `event_type` | `EventType` | **Required** | Event taxonomy enum: `TRADE`, `QUOTE`, `BBO`, `DEPTH`, `BOOK`. |
| `exchange_timestamp`| `float` | **Required** | Exchange matching engine timestamp in fractional Unix epoch seconds (UTC). |
| `receive_timestamp` | `float` | **Required** | Ingress NIC / socket timestamp in fractional Unix epoch seconds (UTC). |
| `processing_timestamp` | `float` | **Required** | Normalization engine timestamp in fractional Unix epoch seconds (UTC). |
| `sequence_number` | `Optional[int]`| **Optional** | Upstream exchange monotonic sequence counter. `None` if unsequenced. |
| `source_sequence` | `Optional[int]`| **Optional** | Feed-handler session sequence counter for gap recovery. |
| `raw_id` | `Optional[str]`| **Required** | Lineage foreign key linking directly to raw archived frame (`raw-{id}`). |
| `price` | `Optional[float]`| **Optional** | Trade price or mid price. Can be negative (e.g. WTI crude oil). |
| `quantity` | `Optional[float]`| **Optional** | Trade or aggregate quote volume. Must be strictly non-negative. |
| `bid` / `bid_price`| `Optional[float]`| **Optional** | Best bid quote price. Can be negative. |
| `ask` / `ask_price`| `Optional[float]`| **Optional** | Best ask quote price. Can be negative. |
| `bid_size` | `Optional[float]`| **Optional** | Best bid quantity. Must be $\ge 0$. |
| `ask_size` | `Optional[float]`| **Optional** | Best ask quantity. Must be $\ge 0$. |
| `clock_source` | `str` | **Required** | Clock origin: `EXCHANGE`, `GATEWAY_RECV`, `PTP_HW`, `SYSTEM`. |
| `feed_source` | `str` | **Required** | Ingress adapter name (e.g. `FEEDX`, `ITCH_DIRECT`, `BINANCE_WS`). |
| `quality_status` | `QualityStatus`| **Required** | Evaluated quality code: `VALID`, `SUSPICIOUS`, `INVALID`. |
| `reasons` | `list[str]` | **Required** | Array of quality reason codes explaining anomaly triggers. |
| `reason_mask` | `int` | **Required** | 64-bit bitmask of triggered rules defined in `rules.def`. |

---

## 2. Identity & Collision Prevention Rules

1. **Non-Truncation Invariant**:
   Under no circumstances shall `instrument_id` or `symbol` be truncated to 8 bytes in protocol frames. 
   Option symbols (21 characters) such as `AAPL260116C00150000` (Call) and `AAPL260116P00150000` (Put) must maintain distinct identities.
2. **Crypto Pair Disambiguation**:
   Spot (`BTC-USDT-SPOT`), Perps (`BTC-USDT-SWAP`), and Futures (`BTC-USDT-260327`) must never collide.
3. **Negative Price Legality**:
   Negative and zero prices are valid financial numbers. Negative prices must never be represented by `None` or filtered out. The presence of a quote is dictated by `bid is not None` and `ask is not None`, not by price sign.
