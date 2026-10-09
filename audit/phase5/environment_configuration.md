# Phase 5 Environment Configuration Matrix

**Date**: 2026-10-09  
**Configuration Standard**: `mdrap.toml` with Environment Variable Overrides  

---

## 1. Configuration Precedence Order

MDRAP evaluates configuration values in strict hierarchical order:
1. **Command-Line Flags** (`--port`, `--role`, `--config`) [Highest Priority].
2. **Environment Variables** (`MDRAP_*`, e.g., `MDRAP_API_KEY_SALT`, `MDRAP_INGEST_LOG_DIR`).
3. **Configuration File** (`mdrap.toml`).
4. **Hardcoded Engine Defaults** [Lowest Priority].

---

## 2. Environment Matrix

| Environment | Mode | Storage Backend | IngestLog Fsync Policy | Logging Level |
| :--- | :--- | :--- | :--- | :--- |
| **Development** | Single-Node | IngestLog (`wal/`) | `grouped_by_time` | `DEBUG` |
| **CI / Automated Test** | Isolated Temp | IngestLog (`tmp/`) | `grouped_by_size` | `INFO` |
| **Controlled Pilot** | Profile A | IngestLog (`/var/lib/mdrap/wal`) | `grouped_by_size` | `INFO` |
| **Production High-Avail** | Profile B | IngestLog (`/var/lib/mdrap/wal`) | `grouped_by_size` / `always` | `WARNING` |
