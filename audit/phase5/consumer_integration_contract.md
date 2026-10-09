# Phase 5 Consumer Integration Contract

**Document Version**: 3.0.0  
**Interchange Format**: Simple Binary Encoding (SBE v1 Fixed 64-Byte Layout)  
**Date**: 2026-10-09  

---

## 1. Binary Wire Framing Protocol

All consumers connect to the canonical MDRAP broadcast stream via TCP, domain socket, or shared memory ring buffer. The stream consists of contiguous 64-byte little-endian binary frames:

```text
Field                  Offset   Size   Type       Encoding / Unit
-------------------------------------------------------------------------
symbol                 0..15    16B    char[16]   ASCII, null-padded
sequence_number        16..23   8B     uint64_t   Monotonic per (source, symbol)
exchange_timestamp_ns  24..31   8B     int64_t    Nanoseconds UTC
price                  32..39   8B     double     IEEE 754 float64
quantity               40..47   8B     double     IEEE 754 float64
event_type             48..51   4B     uint32_t   1 = TRADE, 2 = QUOTE
quality_status         52..55   4B     uint32_t   0 = VALID, 1 = SUSPICIOUS, 2 = INVALID
reserved               56..63   8B     uint8_t[8] Alignment padding (\x00*8)
```

---

## 2. Sequence Continuity & Gap Handling

- **Monotonic Progression**: Sequences increment monotonically ($S_{t+1} = S_t + 1$).
- **Gap Detection**: If $S_{new} > S_{expected}$, a sequence gap has occurred ($Gap = S_{new} - S_{expected}$).
- **Consumer Behavior**:
  1. The consumer must audit the missing count and log a gap warning.
  2. The consumer must NOT stall processing or block other instruments.
  3. The consumer may request a snapshot replay over the REST/reconciliation endpoint if gap exceeds threshold.
