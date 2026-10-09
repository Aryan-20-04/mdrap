# Phase 4 Migration Compatibility Report

**Scope**: Storage schemas, wire formats, configuration schemas, and compatibility guarantees  
**Timestamp**: 2026-10-09  

---

## 1. Schema & Wire Compatibility Matrix

| Component | Format in v2.x | Format in v3.0 | Compatibility Mechanism |
| :--- | :--- | :--- | :--- |
| **Ingress Wire** | JSON / Text frames | JSON / SBE binary | Dual-mode: raw JSON parser with fallback to SBE v1 |
| **Output IPC** | POSIX SHM / Ring | 64-byte SBE v1 | Exact layout with static 64-byte struct alignment |
| **WAL Storage** | SQLite WAL | Segmented IngestLog (`.log`) | Legacy SQLite reader maintained for archival replay |
| **Config File** | `config.yaml` | `mdrap.toml` | Backward-compatibility shim loads YAML with deprecation warning |
| **Metering DB** | In-memory counts | SQLite WAL `metering_records` | Automatic table creation on initialize (`CREATE TABLE IF NOT EXISTS`) |
