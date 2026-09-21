# MDRAP Canonical Hot Record Specification (v1)

- **Status**: FROZEN (Milestone M1 Contract)
- **Document Version**: 1.0.0
- **Endianness**: Little-Endian (LE) exclusively
- **Target Alignment**: 64-byte compact record (1 CPU Cache Line) / 128-byte extended record

---

## 1. Overview & Objectives

In MDRAP v2.1 control-plane Python coordinates data feeds and administrative tasks, but the low-latency hot path is executed by the native core daemon (`mdrapd`).
This specification defines the **fixed-size, cache-line-aligned binary layout** for canonical market events exchanged across the native pipeline, shared-memory rings, and high-performance consumers.

### 1.1 Key Principles
1. **Zero Dynamic Allocation**: Fixed 64-byte struct layout. No pointers, no strings, no heap objects in the hot record.
2. **Deterministic Integer Math**: All prices and quantities are integer fixed-point ticks (`int64_t`). No floating-point rounding ambiguities or NaN signaling in the core hot path.
3. **Interned Identifier Tables**: Strings (symbols, venues, sources) are mapped to 32-bit and 16-bit integer IDs via a side dictionary.
4. **Lineage & Auditability**: Every canonical record contains source sequence, wire timestamp, exchange timestamp, quality verdict, and a 16-bit reason code mask matching `src/rules.def`.

---

## 2. Binary Layout (64-Byte Compact Record)

```
       0                   1                   2                   3
       0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
      +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
0x00  |                        sequence_number                        |
      |                           (uint64_t)                          |
      +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
0x08  |                      exchange_timestamp_ns                    |
      |                            (int64_t)                          |
      +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
0x10  |                        wire_timestamp_ns                      |
      |                            (int64_t)                          |
      +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
0x18  |                             price                             |
      |                        (int64_t ticks)                        |
      +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
0x20  |                            quantity                           |
      |                        (int64_t units)                        |
      +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
0x28  |           symbol_id           |   venue_id    |   source_id   |
      |          (uint32_t)           |  (uint16_t)   |  (uint16_t)   |
      +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
0x30  |  quality  |   flags   |          reason_mask          | (res) |
      |  (uint8)  |  (uint8)  |          (uint16_t)           | 16-bit|
      +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
0x38  |                  reserved / depth telemetry                   |
      |                       (10 bytes padding)                      |
      +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
```

### 2.1 Byte Offset Table

| Offset | Size (B) | Type | Field Name | Description |
|:---:|:---:|:---:|---|---|
| `0x00` | 8 | `uint64_t` | `sequence_number` | Monotonically increasing sequence number per source feed |
| `0x08` | 8 | `int64_t` | `exchange_timestamp_ns` | Exchange matching engine timestamp (nanoseconds UTC) |
| `0x10` | 8 | `int64_t` | `wire_timestamp_ns` | Platform NIC ingress timestamp (nanoseconds UTC) |
| `0x18` | 8 | `int64_t` | `price` | Event price in fixed-point ticks |
| `0x20` | 8 | `int64_t` | `quantity` | Event volume / quantity in fixed-point units |
| `0x28` | 4 | `uint32_t` | `symbol_id` | Interned symbol ID referencing side dictionary |
| `0x2C` | 2 | `uint16_t` | `venue_id` | Interned venue / exchange ID (e.g. 1=NASDAQ, 2=CME) |
| `0x2E` | 2 | `uint16_t` | `source_id` | Interned feed source ID (e.g. 1=ITCH_A, 2=ITCH_B) |
| `0x30` | 1 | `uint8_t` | `quality_status` | `0` = VALID, `1` = SUSPICIOUS, `2` = INVALID |
| `0x31` | 1 | `uint8_t` | `flags` | Bitfield flags (see Section 4) |
| `0x32` | 2 | `uint16_t` | `reason_mask` | Bitmask of anomaly reason codes from `src/rules.def` |
| `0x34` | 12 | `uint8_t[12]` | `reserved` | 12-byte cache line alignment padding (must be zeros) |

**Total Size**: Exactly **64 bytes** ($1 \times \text{cache line}$).

---

## 3. Fixed-Point Numeric Representations

Floating-point representations (`double`, `float`) introduce non-deterministic binary representations across hardware architectures and compiler optimization flags. Canonical Hot Record v1 mandates integer fixed-point scaling:

| Asset Class | Scale Factor | Tick Size | Example Value | Encoded `int64_t` |
|---|:---:|:---:|---|---|
| **Equities** | $10^4$ ($10,000$) | \$0.0001 (1/100 cent) | \$150.25 | `1,502,500` |
| **Cryptocurrency** | $10^8$ ($100,000,000$) | 1 Satoshi / 10 nBTC | \$80,500.12345678 | `8,050,012,345,678` |
| **Futures / FX** | $10^7$ ($10,000,000$) | 0.1 pip / 10 n-index | 5,025.250 | `50,252,500,000` |

---

## 4. Flags and Reason Bitmask

### 4.1 Flags Byte (`uint8_t`)
- `Bit 0` (`0x01`): **CROSSED** — Best bid $\ge$ best ask.
- `Bit 1` (`0x02`): **SIMULATED** — Generated by simulator / fault injector.
- `Bit 2` (`0x04`): **EVENT_TYPE** — `0` = Trade, `1` = Quote update.
- `Bit 3` (`0x08`): **SNAPSHOT** — Book snapshot / state refresh (not incremental delta).
- `Bit 4` (`0x10`): **ARBITRATED** — Resolved via A/B multi-cast arbitration.
- `Bits 5–7`: Reserved for future expansion.

### 4.2 Quality Status (`uint8_t`)
- `0x00`: **VALID** — Passed all structural, sequencing, and statistical checks.
- `0x01`: **SUSPICIOUS** — Anomaly detected (e.g. 3σ price jump, minor cross), eligible for research & logging but quarantined from hot algorithmic execution.
- `0x02`: **INVALID** — Fatal corruption (schema error, negative price, crossed book in strict mode, sequence gap). **Never silently dropped; quarantined.**

### 4.3 Reason Mask (`uint16_t`)
Matches `src/rules.def`:
```c
#define REASON_SCHEMA_VIOLATION         (1 << 0)
#define REASON_DUPLICATE                (1 << 1)
#define REASON_SEQUENCE_GAP             (1 << 2)
#define REASON_OUT_OF_ORDER             (1 << 3)
#define REASON_STALE                    (1 << 4)
#define REASON_PRICE_ANOMALY            (1 << 5)
#define REASON_CROSSED_QUOTE            (1 << 6)
#define REASON_CROSS_FEED_DISAGREEMENT  (1 << 7)
#define REASON_MALFORMED                (1 << 8)
#define REASON_CIRCUIT_FILTER_BREACH    (1 << 9)
#define REASON_VOLATILITY_INTERRUPTION  (1 << 10)
#define REASON_SPECIAL_QUOTE_INDICATION (1 << 11)
#define REASON_TS_IMPLAUSIBLE           (1 << 12)
#define REASON_RATE_LIMITED             (1 << 13)
#define REASON_SECURITY_REJECT          (1 << 14)
```

---

## 5. Side Dictionary Specification

To avoid storing variable-length strings in hot records, strings are interned at startup or upon symbol discovery into an append-only, thread-safe side dictionary.

```json
{
  "version": 1,
  "symbols": {
    "0": "BTC/USD",
    "1": "ETH/USD",
    "2": "AAPL",
    "3": "MSFT"
  },
  "venues": {
    "1": "NASDAQ",
    "2": "CME",
    "3": "BINANCE",
    "4": "COINBASE"
  },
  "sources": {
    "1": "ITCH_FEED_A",
    "2": "ITCH_FEED_B",
    "3": "MDP3_FEED_A",
    "4": "CRYPTO_WS"
  }
}
```

The dictionary is published via shared memory and SQLite control tables, allowing consumers to map `symbol_id` back to human-readable tickers with zero latency penalty on the hot path.
