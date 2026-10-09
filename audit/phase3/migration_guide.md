# MDRAP Phase 3 — Migration Guide (v3.0 -> v3.1)

**Document Identifier**: `MDRAP-MIGRATE-P3-001`  
**Date**: October 9, 2026  

---

## 1. Deprecated Interfaces & Recommended Substitutions

| Deprecated Feature | Modern Replacement | Migration Action |
|---|---|---|
| `from src.journal import BinaryJournal` | `from mdrap.ingestlog import IngestLog` | Replace legacy binary journal with CRC32 IngestLog WAL |
| `Pipeline(store=...)` | `Engine(store=...)` / `Runtime` | Migrate pipeline orchestration to canonical `Runtime` |
| `config.yaml` | `mdrap.toml` | Convert YAML configs to fail-closed TOML schema |
| Custom feed loops | `BaseFeedAdapter` / `ReplayFeedAdapter` | Inherit from `BaseFeedAdapter` in `src/mdrap/ingress.py` |
| Ad-hoc client tracking | `DurableUsageMeter` (`metering.py`) | Use SQLite-backed durable usage accounting |

---

## 2. Backward Compatibility Shims

All legacy imports from root `src/` (`src/ingress.py`, `src/metering.py`, `src/runtime.py`, `src/supervisor.py`) emit deprecation warnings and route to canonical implementations under `src/mdrap/`.
