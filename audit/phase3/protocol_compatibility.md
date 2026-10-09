# MDRAP Phase 3 — Protocol Compatibility Matrix

**Document Identifier**: `MDRAP-PROTO-P3-001`  
**Date**: October 9, 2026  

---

## 1. Supported Wire & Serialization Formats

| Format | Version | Primary Purpose | ABI / Encoding Compatibility |
|---|---|---|---|
| **Simple Binary Encoding (SBE)** | v1.0 | Ultra-low-latency IPC / SHM / TCP | Little-endian, 64-byte aligned, zero-copy C/C++ struct layout. |
| **JSON Framing** | v1.1 | REST / WebSocket client streaming | UTF-8, RFC 8259, ISO 8601 timestamps or numeric epoch nanoseconds. |
| **IngestLog WAL Frames** | v1.0 | Durable write-ahead persistence | `Magic (4B) | Seq (8B) | Len (4B) | CRC32 (4B) | Payload (NB)`. |
| **ITCH 5.0** | v5.0 | NASDAQ direct feed replay & ingress | Standard binary ITCH 5.0 specifications. |

---

## 2. Backward & Forward Compatibility Policy

1. **Additive Schema Updates**: New fields are appended to the end of SBE structures or JSON payloads; readers ignore unrecognized trailing fields.
2. **Version Negotiation**: TCP and WebSocket clients handshake with `version: "3.1.0"`; server rejects incompatible major versions (`MAJOR.x.x`).
3. **Deprecation Window**: Deprecated protocols and fields are retained for a minimum of two minor releases before eviction.
