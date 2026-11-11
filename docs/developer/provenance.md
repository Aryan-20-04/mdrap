# Event Provenance & Lineage Integrity

MDRAP establishes an institutional chain of custody for every market data event. Unlike traditional trading infrastructure that discards raw wire payloads after parsing, MDRAP binds raw frames to canonical state using cryptographic hashes.

---

## 1. Why Cryptographic Provenance Matters

In modern quantitative trading, backtesting anomalies, regulatory inquiries, and dispute settlements often require proving:
1. Exactly what wire message was received from an exchange.
2. Exactly when it arrived (microsecond timestamp vector).
3. How the normalizer interpreted the frame.
4. What data-quality rules evaluated the event.
5. That historical records were not altered after the fact.

MDRAP addresses this by generating an immutable `EventProvenance` record for every tick.

---

## 2. Provenance Architecture

```
+-------------------------------------------------------------------------------+
| Raw Wire Frame (JSON / SBE bytes)                                             |
| {"type":"ticker","sequence":1003,"bid":"65200.0","ask":"65200.5"}             |
+-------------------------------------------------------------------------------+
                                      |
                                      v
                      SHA-256(Raw Wire) = PayloadHash
                                      |
                                      +------------------------------------+
                                      |                                    |
                                      v                                    v
                        CanonicalEvent Normalization          Tri-Timestamp Vector
                        (Instrument, Prices, Sizes)           (exchange, receive, process)
                                      |                                    |
                                      +-----------------+------------------+
                                                        |
                                                        v
                                             15-Rule Quality Engine
                                             (Status: VALID | SUSPICIOUS)
                                                        |
                                                        v
+-------------------------------------------------------------------------------+
| Lineage Hash Generation                                                       |
| SHA-256(EventID : RawID : Venue : Instrument : Mode : PayloadHash : Status : Ts)|
+-------------------------------------------------------------------------------+
```

---

## 3. The `EventProvenance` Record

Defined in `mdrap.provenance.EventProvenance`:

```python
@dataclass(slots=True)
class EventProvenance:
    event_id: str             # Unique monotonic event identifier
    raw_id: str               # Raw frame identifier
    provider: str             # e.g., "COINBASE", "KRAKEN"
    instrument: str           # e.g., "BTC-USD"
    channel: str              # e.g., "ORDERBOOK_L1"
    data_mode: str            # "LIVE", "REPLAY", "SIMULATION"
    exchange_timestamp: float # Timestamp reported by venue
    receive_timestamp: float  # Microsecond host receive timestamp
    processing_timestamp: float # Pipeline processing timestamp
    sequence_number: Optional[int] # Monotonic exchange sequence counter
    parser_version: str       # Version of MDRAP parser (e.g., "6.0.0")
    normalizer_version: str   # Version of MDRAP normalizer (e.g., "6.0.0")
    quality_status: str       # "VALID", "SUSPICIOUS", "INVALID"
    quality_reasons: list[str]# List of triggered anomaly reasons
    payload_sha256: str       # SHA-256 digest of original wire frame
    processing_status: str    # "PROCESSED", "QUARANTINED", "DROPPED"
    durability_status: str    # "COMMITTED_MEM", "COMMITTED_WAL", "DURABLE_DISK"
    lineage_hash: str         # Deterministic end-to-end lineage digest
```

---

## 4. Mathematical Determinism of the Lineage Hash

The `lineage_hash` guarantees that neither the raw payload, normalizer version, timestamps, nor quality evaluation outcome can be altered without invalidating the digest:

$$\text{LineageHash} = \text{SHA256}(\text{event\_id} \parallel \text{raw\_id} \parallel \text{provider} \parallel \text{instrument} \parallel \text{data\_mode} \parallel \text{payload\_sha256} \parallel \text{quality\_status} \parallel \text{processing\_timestamp})$$

Any modification to any of these fields immediately causes a mismatch during verification.

---

## 5. Storage and Retention

Provenance records are committed to SQLite in batches using `executemany` into the `provenance` table:

```sql
CREATE TABLE IF NOT EXISTS provenance (
    event_id TEXT PRIMARY KEY,
    raw_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    instrument TEXT NOT NULL,
    channel TEXT NOT NULL,
    data_mode TEXT NOT NULL,
    exchange_timestamp REAL,
    receive_timestamp REAL,
    processing_timestamp REAL,
    sequence_number INTEGER,
    parser_version TEXT,
    normalizer_version TEXT,
    quality_status TEXT,
    quality_reasons TEXT,
    payload_sha256 TEXT NOT NULL,
    processing_status TEXT,
    durability_status TEXT,
    lineage_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_prov_provider ON provenance(provider, instrument);
CREATE INDEX IF NOT EXISTS idx_prov_lineage ON provenance(lineage_hash);
```
