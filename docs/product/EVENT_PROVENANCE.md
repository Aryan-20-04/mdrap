# MDRAP Event Provenance & Forensic Lineage Layer

**Document Version**: 2.0  
**Responsible Agent**: Agent 3 (Event Provenance & Integrity Infrastructure)  
**Status**: VERIFIED IMPLEMENTATION  

---

## 1. Architectural Mission

The Event Provenance layer is the trust foundation of MDRAP. Its primary function is to eliminate the question:
*"What exact data did our trading system receive at that specific millisecond, where did it come from, and has it been altered?"*

Every ingested event is converted into a cryptographically anchored record that binds the raw wire bytes to its normalized representation, quality classification, and persistent storage offset.

---

## 2. Event Provenance Schema (`EventProvenance`)

```python
@dataclass(slots=True)
class EventProvenance:
    event_id: str                 # Monotonic unique identifier (e.g. evt-18873c351fc0-1)
    raw_id: str                   # Raw vendor ingress frame identifier
    provider: str                 # Source venue (e.g. BINANCE, BYBIT, KRAKEN, OKX)
    instrument: str               # Canonical symbol (e.g. BTC/USD, ETH/USD)
    channel: str                  # Upstream market channel (e.g. ORDERBOOK_L1, TRADE)
    data_mode: str                # Strict data provenance: "LIVE", "REPLAY", "SIMULATION"
    exchange_timestamp: float     # Exchange gateway clock (seconds since Unix epoch)
    receive_timestamp: float      # MDRAP local NIC ingress time
    processing_timestamp: float   # Quality engine completion time
    sequence_number: int | None   # Monotonic integer sequence from exchange
    parser_version: str           # Software version of the frame unmarshaler
    normalizer_version: str       # Software version of the canonical normalizer
    quality_status: str           # VALID | SUSPICIOUS | INVALID
    quality_reasons: list[str]    # Explicit anomaly codes (e.g. ["SEQUENCE_GAP", "TS_IMPLAUSIBLE"])
    payload_sha256: str           # SHA-256 hash of original untouched wire bytes
    processing_status: str        # PROCESSED | QUARANTINED | DROPPED
    durability_status: str        # COMMITTED_MEM | COMMITTED_WAL | DURABLE_DISK
    lineage_hash: str             # Deterministic hash binding raw frame, metadata, and quality state
```

---

## 3. Cryptographic Lineage & Binding

The `lineage_hash` is computed as:
$$\text{lineage\_hash} = \text{SHA-256}(\text{event\_id} : \text{raw\_id} : \text{provider} : \text{instrument} : \text{data\_mode} : \text{payload\_sha256} : \text{quality\_status} : \text{processing\_timestamp})$$

This mathematical binding ensures that:
1. **Tamper Evidence**: Any post-hoc modification to the database row (e.g. altering the bid price, changing `INVALID` to `VALID`, or altering timestamps) breaks the lineage hash.
2. **Deterministic Reconstruction**: Given the raw wire payload and configuration, re-executing the normalizer must produce the exact same `payload_sha256` and canonical fields.

---

## 4. Limits of Cryptographic Evidence (Honest Assurance)

It is critical to document the boundaries of what MDRAP's cryptographic evidence proves:
- **What it Proves**: An MDRAP hash proves beyond mathematical doubt that the data recorded by MDRAP has not been modified, corrupted, or backdated since the moment of local ingress commitment.
- **What it Does NOT Prove**: Unless an external exchange signs each tick with an asymmetric private key (e.g. Ed25519/ECDSA), an MDRAP hash cannot independently prove that the exchange's matching engine originally emitted those exact bytes. It proves the local NIC received them over the authenticated TLS session.

---

## 5. Durability Tiers & Asynchronous Commitments

To prevent cryptographic hashing from slowing down the 500,000 EPS hot path:
1. **Ingress Hot Path (T0)**: Compute fast 64-bit CRC32C and lightweight SHA-256 on wire payloads in C/Python.
2. **Memory Ring (T1)**: Buffer provenance records in an in-memory lock-free tracker (`ProvenanceTracker`, 50,000 records).
3. **Write-Ahead Log (T2)**: Grouped append to WAL with micro-batch fsync policies (`grouped_by_time`, 10ms batches).
4. **Permanent Audit (T3)**: Relational projection into SQLite `lineage` and `quarantine` tables with indexed lookups.
