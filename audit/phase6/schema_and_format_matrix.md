# MDRAP Phase 6 — Schema & Message Serialization Format Matrix

## 1. Executive Summary & Architecture
To ensure high-throughput low-latency execution while maintaining cross-system interoperability, MDRAP uses distinct data serialization formats tailored for specific pipeline boundaries:
- **Hot Transport & IPC**: Simple Binary Encoding (SBE) and POSIX/Win32 Shared Memory.
- **Ingress Feeds**: Raw binary exchange protocols (ITCH 5.0) and JSON/WebSocket feeds (Binance, Kraken, Databento).
- **Persistence & Auditing**: Binary WAL IngestLog, SQLite 3 (WAL mode), and columnar Parquet.

---

## 2. Serialization Format Compatibility Matrix

| Pipeline Boundary | Data Format | Latency / Overhead | Schema Evolution Policy | Forward / Backward Compatibility |
| :--- | :--- | :--- | :--- | :--- |
| **Ingress Gateways** | Raw ITCH / JSON | High parse overhead for JSON ($< 15\text{ \mu s}$) | Strict field validation per adapter | Backward compatible via fallback defaults |
| **Hot Path Core IPC** | Simple Binary Encoding (SBE) | Zero-copy / $< 0.1\text{ \mu s}$ overhead | Fixed header + trailing optional fields | Full forward/backward compatibility |
| **Shared Memory (SHM)** | Seqlock Binary Structs | Zero-copy seqlock ($< 15\text{ ns}$) | Fixed 64-byte aligned structs | Strict binary layout lockstep |
| **IngestLog WAL** | Append-only Binary Frame | Microsecond atomic fsync | 16-byte fixed header + CRC32 | Fully forward compatible via header flags |
| **Operational Query** | SQLite 3 Relational Tables | Indexed B-Tree | Monotonic schema migration scripts | Backward compatible via `ALTER TABLE` |
| **Cold Storage** | Columnar Parquet + Zstd | High throughput batch analytics | Schema embedded in Parquet metadata | Full schema evolution supported |

---

## 3. SBE Wire Format Binary Specification (142 Bytes)

```
Offset (Bytes)  Data Type       Field Name          Description
──────────────  ──────────────  ──────────────────  ───────────────────────────────────────
0..3            uint32          message_size        Total binary payload length
4..5            uint16          template_id         101=Trade, 102=Quote, 103=OrderBook
6..7            uint16          schema_version      Monotonic schema version (Current: 1)
8..15           uint64          sequence_number     Strictly monotonic shard sequence
16..23          uint64          exchange_timestamp  Nanoseconds since Unix epoch
24..31          uint64          receive_timestamp   Nanoseconds since Unix epoch
32..47          char[16]        instrument_symbol   Null-padded UTF-8 symbol string (e.g. AAPL)
48..55          int64           price_fixed_point   Price multiplied by 10^8 (8 decimal scale)
56..63          int64           quantity_fixed      Size multiplied by 10^4 (4 decimal scale)
64..65          uint16          venue_id            Numeric identifier for source exchange
66..67          uint16          event_flags         Bitmask: 0x01=Aggressive, 0x02=Crossed
68..71          uint32          quality_status      0=VALID, 1=SUSPICIOUS, 2=INVALID
72..75          uint32          epoch_generation    Cluster epoch generation counter
76..141         uint8[66]       extension_padding   Reserved alignment padding for v2+ fields
```

---

## 4. Schema Evolution Rules & Non-Negotiables
1. **Never Reorder Existing Fields**: Field byte offsets in SBE headers are immutable across all minor and patch versions.
2. **Trailing Extensions Only**: All new fields must be allocated from the 66-byte `extension_padding` area at the tail of the message.
3. **Fixed-Point Scaling Invariant**: Prices must always use 8 decimal places ($10^8$ multiplier); quantities must always use 4 decimal places ($10^4$ multiplier). Floating-point IEEE-754 serialization is explicitly forbidden on hot IPC links.
4. **Unknown Template Handling**: Consumer SDKs encountering unrecognized `template_id` values must skip the message using `message_size` without raising fatal deserialization exceptions.
