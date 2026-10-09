# MDRAP Phase 3 — Version Compatibility Specification

**Document Identifier**: `MDRAP-VERCOMPAT-P3-001`  
**Date**: October 9, 2026  

---

## 1. Version Identifiers across Subsystems

- **Platform Release Version**: `3.1.0` (Semantic Versioning 2.0.0)
- **IngestLog Segment Version**: `1`
- **Binary SBE Frame Protocol**: `1.0`
- **Native Consumer C ABI**: `1.0`
- **Compliance Metering Schema**: `1.0`
- **Failover Heartbeat Protocol**: `1.0`

---

## 2. Compatibility Guarantees

- **Minor Version Upgrades (`3.1.x` -> `3.2.0`)**: Backwards compatible across all storage engines, WAL files, configuration files, and consumer SDK ABIs.
- **Major Version Upgrades (`3.x.x` -> `4.0.0`)**: May introduce schema migrations with automated upgrade scripts.
