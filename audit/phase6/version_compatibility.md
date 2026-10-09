# MDRAP Phase 6 — Cross-Deployment Version & Schema Compatibility Matrix

## 1. Executive Summary & Policy Scope
In multi-node and multi-consumer institutional deployments, version skew across consumer SDKs, engine runtimes, and wire schemas is inevitable. This specification defines the supported compatibility combinations across all platform components and the rules governing forward and backward binary compatibility.

---

## 2. Platform Compatibility Matrix

| Component | Minimum Supported Version | Target Phase 6 Version | Compatibility Status | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **MDRAP Core Runtime** | `v1.0.0-rc4` | `v1.0.0-phase6` | Fully Compatible | Core engine protocol version `1`. |
| **SBE Wire Protocol Schema** | Schema v1.0 | Schema v1.1 | Backward Compatible | Optional trailing fields ignored by v1.0 readers. |
| **C++ SDK (`mdrap-cpp`)** | v1.0.0 | v1.1.0 | Compatible | Zero memory layout breakage in structs. |
| **Rust SDK (`mdrap-rs`)** | v0.9.0 | v1.0.0 | Compatible | Safe zero-copy deserialization contracts. |
| **Java SDK (`mdrap-java`)** | v1.0.0 | v1.0.0 | Compatible | Agrona DirectBuffer SBE decoder parity. |
| **IngestLog WAL Format** | Segment Format v2 | Segment Format v2 | **100% Binary Identical** | CRC32 frame header unchanged. |
| **SQLite Canonical DB** | Schema v3 | Schema v3 | **100% Binary Identical** | Append-only tables; zero destructive migrations. |

---

## 3. Forward and Backward Wire Compatibility Invariants

1. **SBE Schema Evolution Rules**:
   - Fields may only be added to the end of message schemas.
   - Field offsets and existing field types are strictly immutable.
   - Older SDK consumers reading a newer stream read up to their known block length and safely ignore trailing bytes.
2. **Deprecation Cycle**:
   - Any public API or protocol field slated for removal requires a minimum **6-month formal deprecation notice** with logging warnings before removal.
