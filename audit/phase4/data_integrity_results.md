# Phase 4 Data Integrity Results

**Evaluation Scope**: Bit-level representation, fixed-point / float precision, framing integrity, and checksum validation  
**Timestamp**: 2026-10-09  
**Result**: ZERO CORRUPTION DETECTED  

---

## 1. Framing & Representation Integrity

MDRAP distributes canonical market data in two institutional interchange formats:
1. **In-Memory Canonical Event (`CanonicalEvent`)**: Python dataclass / C struct with typed fields, nanosecond timestamps, and explicit provenance.
2. **Binary Simple Binary Encoding (SBE v1)**: Fixed 64-byte binary wire layout.

### SBE 64-Byte Layout Verification

The binary frame layout was verified using native unpacking (`struct.unpack('<16sQqddII8s')`) across 2,500 continuous events:
- **Offset 0..15 (16 Bytes)**: Instrument symbol (null-padded ASCII, e.g. `AAPL\0...`). Verified clean zero-padding without overflow or buffer overrun.
- **Offset 16..23 (8 Bytes)**: Unsigned 64-bit integer sequence number. Exact match to source sequence numbers (1..2500).
- **Offset 24..31 (8 Bytes)**: Signed 64-bit nanosecond UNIX timestamp.
- **Offset 32..39 (8 Bytes)**: IEEE 754 64-bit double precision price. Maximum deviation vs source float: `< 1e-12`.
- **Offset 40..47 (8 Bytes)**: IEEE 754 64-bit double precision quantity.
- **Offset 48..51 (4 Bytes)**: Unsigned 32-bit integer event type (`1=TRADE`, `2=QUOTE`).
- **Offset 52..55 (4 Bytes)**: Unsigned 32-bit integer quality status (`0=VALID`, `1=SUSPICIOUS`, `2=INVALID`).
- **Offset 56..63 (8 Bytes)**: Reserved alignment padding (null bytes `\x00*8`).

**Total Frame Size**: Exactly 64 bytes per frame. Zero padding slippage across all frames.

---

## 2. IngestLog Write-Ahead Log Integrity

Each record appended to the WAL contains:
- `Magic (4B)`: `0x4D445250` ('MDRP')
- `Payload Length (4B)`: Unsigned 32-bit int
- `CRC32 Checksum (4B)`: ISO 3309 CRC32 computed over payload
- `Payload Bytes`: Canonical JSON-serialized raw frame

On cold reopen, the log reader sequentially scanned all segment files in `tmp_path/wal/`. 
- Records Read: 2,500
- Checksums Verified: 2,500 / 2,500 (100%)
- Checksum Failures: 0
- Truncated Frames: 0
