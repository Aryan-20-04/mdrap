# MDRAP Data Model Specification

Based on the *Market Data Reliability & Acceleration Platform Reference (Section 4, 17, 26)*.

## 1. Core Philosophy

Every piece of data flows through a strict lifecycle:
1. **Raw Ingestion**: Captures raw byte/dict payload and exact gateway receive time.
2. **Canonical Normalization**: Standardized schema across all asset classes and vendors.
3. **Quality Annotation**: Non-destructive annotation with quality status and reasons.
4. **Lineage Preservation**: Complete provenance linking the final decision back to source inputs.

---

## 2. Event Schemas

### 2.1 `RawEvent` (`src/models.py`)
Unnormalized record direct from the feed or simulator:
| Field | Type | Description |
|---|---|---|
| `source` | `str` | Name of the feed provider (e.g. `FEEDX`, `FEEDY`) |
| `payload` | `dict` | Vendor-specific payload dictionary |
| `receive_timestamp` | `float` | High-precision epoch timestamp stamped at gateway arrival |
| `raw_id` | `str` | Unique monotonic identifier (`sim-N` or `raw-N`) |

### 2.2 `CanonicalEvent` (`src/models.py`)
Normalized canonical representation:
| Field | Type | Description |
|---|---|---|
| `event_id` | `str` | Monotonic unique canonical event ID (`evt-N`) |
| `instrument_id` | `str` | Canonical ticker/symbol (e.g. `AAPL`, `MSFT`) |
| `event_type` | `EventType` | Enum: `TRADE` or `QUOTE` |
| `exchange_timestamp` | `float` | Original timestamp reported by the exchange |
| `receive_timestamp` | `float` | Gateway receipt timestamp |
| `processing_timestamp`| `float` | Pipeline completion timestamp |
| `source` | `str` | Source feed identifier |
| `sequence_number` | `Optional[int]` | Feed sequence counter, if provided by source |
| `price` | `Optional[float]` | Trade price (trades only) |
| `quantity` | `Optional[float]` | Trade volume/shares |
| `bid_price` / `bid_size` | `Optional[float]` | Top-of-book best bid price and size (quotes) |
| `ask_price` / `ask_size` | `Optional[float]` | Top-of-book best ask price and size (quotes) |
| `quality_status` | `QualityStatus` | Enum: `VALID`, `SUSPICIOUS`, `INVALID` |
| `reasons` | `list[str]` | List of reason code strings triggered |
| `raw_id` | `str` | Foreign key referencing the originating raw event |

---

## 3. Deduplication Key Semantics

A deduplication key determines identity within the bounded LRU window:
- **With sequence number:** `(source, instrument_id, sequence_number)`
- **Trades without sequence:** `(source, instrument_id, 'TRADE', round(exchange_ts, 6), price, quantity)`
- **Quotes without sequence:** `(source, instrument_id, 'QUOTE', round(exchange_ts, 6), bid_price, ask_price, bid_size, ask_size)`

---

## 4. Storage Tables (`src/storage.py`)

- **`canonical_events`**: Stores only events where `quality_status != 'INVALID'`.
- **`quarantine`**: Stores corrupted or invalid events (`raw_id`, `source`, `reason`, `raw_payload`, `received_at`).
- **`lineage`**: Audit trail (`event_id`, `raw_id`, `source`, `decision`, `reasons`, `reliability_score`, `created_at`).
- **`source_health`**: Longitudinal source reliability snapshots (`source`, `score`, `completeness`, `accuracy`, `latency_p95`, `recorded_at`).
