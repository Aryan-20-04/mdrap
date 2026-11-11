# MDRAP Consolidation Baseline — Stage 1 Audit

**Platform & Environment**:
- **Date**: 2026-11-11
- **Branch**: `cleanup/product-scope-consolidation`
- **Base Commit**: `384e60b`
- **OS**: Windows (win32)
- **Python**: 3.13.1
- **Pytest**: 9.2.0

---

## 1. Baseline Inventory

### Source Tree Metrics
- `src/*.py`: 94 files (93 forwarding shims + `src/__init__.py`)
- `src/mdrap/*.py`: 109 modules
- `src/mdrap/cli/*.py`: 8 modules
- `src/mdrap/adapters/*.py`: 8 modules
- `tests/*.py`: 294 root test modules (311 test files including subdirectories)
- Total Source (`src`): 226 files, 78,979 lines of code
- Total Tests (`tests`): 311 files, 61,022 lines of code

### Test Execution Baseline
- **Collected Tests**: 1,828 test items
- **Selected Tests**: 1,765 test items (63 deselected by marks)
- **Pilot Assurance & Security Profile**: 16/16 PASSED in 3.58s (`tests/test_pilot_readiness.py`, `tests/test_phase2_*.py`)
- **Product Assurance & Core CLI**: 33/33 PASSED in 3.81s (`tests/test_product_assurance.py`, `tests/test_cli.py`)

### Package Build Baseline
- Package build via `build --wheel --no-isolation`: Succeeded (`mdrap_core-5.3.0-py3-none-any.whl`).
- Packaging manifest inspection confirms that `pyproject.toml` exclusively packages `src/mdrap/**`. None of the 94 root forwarding shims in `src/*.py` are distributed in the wheel package.

---

## 2. Active External Dependencies

| Dependency | Required / Optional | Declared In | Purpose |
| :--- | :--- | :--- | :--- |
| `rich` | Optional (Core UI) | `requirements.txt`, `pyproject.toml [ui]` | Console tables, telemetry rendering (with stdlib fallback in `term.py`) |
| `websockets` | Optional (Feeds) | `requirements.txt`, `pyproject.toml [feeds]` | Real-time WebSocket feed handlers for Coinbase/Binance |
| `fastapi` | Optional (API) | `requirements.txt`, `pyproject.toml [api]` | REST and WebSocket sidecar distribution server |
| `uvicorn` | Optional (API) | `requirements.txt`, `pyproject.toml [api]` | ASGI web server |
| `httpx` | Optional (API) | `requirements.txt`, `pyproject.toml [api]` | Async HTTP client for webhook delivery |
| `pyyaml` | Optional (Config) | `requirements.txt`, `pyproject.toml [config]` | YAML configuration parser (candidate for stdlib `tomllib`/`json` migration) |
| `duckdb` | Optional (Analytics) | `requirements.txt`, `pyproject.toml [columnar]` | Embedded OLAP query engine for tick archives |
| `pyarrow` | Optional (Export) | `requirements.txt`, `pyproject.toml [export]` | Parquet file serialization |

---

## 3. Discovered Legacy & Bloat Clusters

1. **94 Root-Level Shims (`src/*.py`)**:
   - 93 forwarding stubs delegating via dynamic `sys.modules[__name__] = importlib.import_module('mdrap.<name>')`.
   - 66 are completely unreferenced across the entire repository.
   - 27 were referenced primarily by legacy test files, old benchmark scripts, and `src/__init__.py`.
   - None are shipped in the production wheel.
2. **Terminal Navigation & Interactive TUI**:
   - `src/mdrap/navigator.py` (1,234 lines): Excel/Vim style modal trading desk navigator.
   - `src/mdrap/terminal_display.py` (1,238 lines): Interactive curses terminal sparklines & charts.
   - `src/mdrap/trading_cli.py` (776 lines): Trading desk subcommands, backtest CLI, and portfolio models.
   - `src/mdrap/tui.py` (236 lines): Textual / terminal UI loop.
3. **Out-of-Scope Stubs**:
   - `src/mdrap/vessel.py` (78 lines): Maritime AIS tracking forwarding stub.
   - `src/mdrap/options.py` (105 lines): Derivatives pricing / Greeks forwarding stub.
   - `src/mdrap/fx.py` (116 lines): Hardcoded triangular FX arbitrage matrix.
   - `src/mdrap/af_xdp.py` (227 lines): Incomplete kernel-bypass mock without OS driver.
   - `cmd_edgar` in `src/mdrap/cli/integrations.py`: SEC corporate filing mock.
4. **Transport Subsystems**:
   - `src/mdrap/pcap.py` (284 lines): Real Libpcap 2.4 / MoldUDP64 multicast packet dissector. Functional diagnostic tool, not an execution engine.
   - `src/mdrap/kafka_sink.py` (467 lines): Checkpointed SQLite WAL tailer for external stream distribution.
