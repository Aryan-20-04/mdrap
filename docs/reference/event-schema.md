# Event Schema & Storage Reference

This document specifies the exact in-memory dataclass schemas, binary Write-Ahead Log (WAL) framing, and SQLite relational table definitions used in MDRAP.

---

## 1. Dataclass Models

### `mdrap.models.RawEvent`
Captures raw wire payloads as received from network adapters:
```python
@dataclass(slots=True)
class RawEvent:
    source: str                 # Feed source name (e.g. "BINANCE", "ITCH")
    payload: dict               # Parsed JSON/dict or SBE field map
    receive_timestamp: float    # Local clock time at ingress
    raw_id: str = ""            # Lineage tracking identifier
    wire: str | bytes = ""      # Raw untouched bytes or string
    session_id: str | None = None
```

### `mdrap.models.CanonicalEvent`
Normalized, validated market data record:
```python
@dataclass(slots=True)
class CanonicalEvent:
    event_id: str
    instrument_id: str
    event_type: EventType       # TRADE, QUOTE, BBO, BOOK, DEPTH
    exchange_timestamp: float
    receive_timestamp: float
    processing_timestamp: float
    source: str
    sequence_number: int | None
    price: float | None
    quantity: float | None
    bid_price: float | None
    bid_size: float | None
    ask_price: float | None
    ask_size: float | None
    quality_status: QualityStatus  # VALID, SUSPICIOUS, INVALID
    reasons: list[str]
    raw_id: str
    source_id: int
    instrument_id_int: int
    session_id: str | None
```

---

## 2. IngestLog Binary WAL Segment Framing (v2)

Each WAL segment file (`segment_*.wal`) consists of an append-only sequence of variable-length binary records. Each record starts with a 32-byte fixed header:

```text
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|       Magic 0x4D445250        | Version (u16) | RecordType(u16)|
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                       Payload Length (u32)                    |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                     CRC-32 Checksum (u32)                     |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                    Sequence Number (u64)                      |
|                                                               |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                   Ingress Timestamp (u64 ns)                  |
|                                                               |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                        Payload Bytes ...                      |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
```

- **Magic**: `0x4D445250` (ASCII `"MDRP"`).
- **CRC-32**: IEEE 802.3 32-bit cyclic redundancy check computed across header prefix and full payload.
- **Atomic fsync**: Segments are rotated at configured byte limits (default: 64MB) with directory fsync.

---

## 3. SQLite Relational Schema

MDRAP projects canonical state into SQLite with Write-Ahead Logging (`PRAGMA journal_mode=WAL`):

### `canonical_events`
Stores valid and suspicious market events:
```sql
CREATE TABLE IF NOT EXISTS canonical_events (
    event_id TEXT PRIMARY KEY,
    instrument_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    exchange_timestamp REAL NOT NULL,
    receive_timestamp REAL NOT NULL,
    processing_timestamp REAL NOT NULL,
    source TEXT NOT NULL,
    sequence_number INTEGER,
    price REAL,
    quantity REAL,
    bid_price REAL,
    bid_size REAL,
    ask_price REAL,
    ask_size REAL,
    quality_status INTEGER NOT NULL,
    reasons TEXT,
    raw_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_sym_ts ON canonical_events (instrument_id, exchange_timestamp);
```

### `quarantine`
Stores rejected/invalid events for non-destructive auditing:
```sql
CREATE TABLE IF NOT EXISTS quarantine (
    event_id TEXT PRIMARY KEY,
    instrument_id TEXT,
    reasons TEXT NOT NULL,
    raw_payload TEXT NOT NULL,
    quarantine_timestamp REAL NOT NULL,
    raw_id TEXT
);
```

### `audit_log`
Append-only cryptographic SHA-256 hash chain:
```sql
CREATE TABLE IF NOT EXISTS audit_log (
    sequence_id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    actor TEXT NOT NULL,
    role TEXT NOT NULL,
    action TEXT NOT NULL,
    details TEXT,
    prev_hash TEXT NOT NULL,
    entry_hash TEXT NOT NULL
);
```
