# MDRAP Phase 3 — Serialization Test Vectors

**Document Identifier**: `MDRAP-VECTORS-P3-001`  
**Date**: October 9, 2026  

---

## 1. Binary SBE Frame Vector (64 Bytes Aligned)

A canonical Trade Event with:
- `instrument`: `"AAPL"` (padded to 16 bytes)
- `sequence`: `1042`
- `exchange_ts`: `1728475200000000000` (ns)
- `price`: `150.25`
- `quantity`: `100.0`
- `quality_flag`: `0` (VALID)

### Binary Layout:
```
Offset  Size  Field          Value (Hex / Little-Endian)
0x00    16B   instrument     41 41 50 4c 00 00 00 00 00 00 00 00 00 00 00 00
0x10    8B    sequence       12 04 00 00 00 00 00 00 (1042)
0x18    8B    exchange_ts    00 f0 1f b1 47 cc f8 17 (1728475200000000000)
0x20    8B    price          00 00 00 00 00 c8 62 40 (150.25 double)
0x28    8B    quantity       00 00 00 00 00 00 59 40 (100.0 double)
0x30    4B    event_type     01 00 00 00 (1 = TRADE)
0x34    4B    quality_flag   00 00 00 00 (0 = VALID)
0x38    8B    padding        00 00 00 00 00 00 00 00
Total: 64 Bytes
```

---

## 2. JSON Frame Vector

```json
{
  "event_id": "1042",
  "instrument_id": "AAPL",
  "event_type": "TRADE",
  "source": "NASDAQ",
  "feed_id": "MOLD64",
  "sequence": 1042,
  "exchange_ts": 1728475200000000000,
  "receive_ts": 1728475200000045000,
  "price": 150.25,
  "size": 100.0,
  "bid": null,
  "ask": null,
  "quality_flag": 0,
  "reasons": []
}
```
