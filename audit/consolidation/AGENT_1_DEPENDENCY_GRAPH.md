# MDRAP Consolidation — Agent 1: Repository Dependency Graph & Dead-Code Audit

## 1. Executive Summary

Agent 1 performed an exhaustive static and dynamic import audit across the entire MDRAP repository (226 source files, 311 test files, 140,000 lines of code).

Key Findings:
1. **66 of the 93 root-level forwarding shims (`src/*.py`) are completely unreferenced** anywhere in source, tests, benchmarks, or packaging.
2. **27 root-level forwarding shims were referenced by legacy scripts and tests** (`_version`, `cli`, `features`, `fix_engine`, `options`, `risk`, etc.). All 27 can be redirected to `mdrap.*` with zero functional loss.
3. **Four major bloat modules (`navigator.py`, `terminal_display.py`, `trading_cli.py`, `tui.py`) account for 3,484 lines of code** with zero downstream dependencies in the core reliability pipeline.
4. **Three peripheral stubs (`vessel.py`, `options.py`, `fx.py`) were already deprecated** in v5.3.0 and serve no purpose in the market data reliability sidecar.
5. **`af_xdp.py` is a mock stub** with zero consumers in `src/mdrap/`.
6. **`kafka_sink.py` and `pcap.py` provide legitimate decoupled capabilities**: Kafka is a distributed event delivery sink, while PCAP is a diagnostic wire packet dissector.

---

## 2. Dependency Graphs

### A. Core Module Dependency Graph

```
                  +-----------------------------------+
                  |         mdrap.models              |
                  |  (CanonicalEvent, QualityStatus)  |
                  +-----------------------------------+
                                    ^
       +----------------------------+-----------------------------+
       |                            |                             |
+--------------+             +--------------+              +--------------+
| mdrap.gateway|             | mdrap.quality|              | mdrap.storage|
| (Normalization             | (7 Validation|              | (SQLite WAL  |
|  & Ingress)  |             |  Rules)      |              |  Persistence)|
+--------------+             +--------------+              +--------------+
       |                            |                             |
       +----------------------------+-----------------------------+
                                    |
                                    v
                  +-----------------------------------+
                  |        mdrap.provenance           |
                  |  (SHA-256 Payload & Lineage)      |
                  +-----------------------------------+
                                    |
                                    v
                  +-----------------------------------+
                  |        mdrap.incidents            |
                  |  (Anomaly Detection & Tombstones) |
                  +-----------------------------------+
                                    |
                                    v
                  +-----------------------------------+
                  |         mdrap.replay              |
                  |  (Deterministic State Replay)     |
                  +-----------------------------------+
                                    |
                                    v
                  +-----------------------------------+
                  |        mdrap.security             |
                  |  (RFC 6962 Merkle Tree Audit)     |
                  +-----------------------------------+
```

### B. CLI Command Dependency Graph

| Command | Backing Handler | Primary Dependencies | Status |
| :--- | :--- | :--- | :--- |
| `status` | `cli.operations.cmd_status` | `Store`, `Watchdog`, `Engine` | RETAIN |
| `version` | `cli.operations.cmd_version` | `_version.__version__` | RETAIN |
| `doctor` | `cli.operations.cmd_doctor` | `fastpath`, `shm`, `Store` | RETAIN |
| `replay` | `cli.operations.cmd_replay` | `IncidentReplayEngine`, `Store` | RETAIN |
| `audit` | `cli.security.cmd_audit` | `MerkleAuditTree`, `Store` | RETAIN |
| `verify` | `cli.security.cmd_verify` | `MerkleAuditTree`, `tools.verify_evidence` | RETAIN |
| `security` | `cli.security.cmd_security` | `check_read_only_safety`, `APIKeyManager` | RETAIN |
| `chaos` | `cli.operations.cmd_chaos` | `ChaosDrillEngine` | RETAIN |
| `export` | `cli.operations.cmd_export` | `Store`, `IngestLog` | RETAIN |
| `incident` | `cli.operations.cmd_incident` | `IncidentManager`, `Store` | RETAIN |
| `desk` / `nav` | `cli.integrations.cmd_desk` | `navigator.MDRAPNavigator` | DELETE |
| `chart` | `cli.market.cmd_chart` | `terminal_display.render_chart` | DELETE |
| `ticker` | `cli.market.cmd_ticker` | `terminal_display.TickerDashboard` | DELETE |
| `tui` | `cli.operations.cmd_tui` | `tui.App` | DELETE |
| `vessel` | `cli.integrations.cmd_vessel` | `vessel.VesselTracker` | DELETE |
| `edgar` | `cli.integrations.cmd_edgar` | None (Deprecation notice) | DELETE |
| `backtest` | `trading_cli.cmd_backtest` | `backtest.BacktestEngine` | DELETE |
| `portfolio` | `trading_cli.cmd_portfolio` | `trading_cli.PortfolioTracker` | DELETE |

### C. Test-to-Module Mapping for Candidate Deletions

| Candidate File | Associated Dedicated Tests | Shared Core Tests Affected? |
| :--- | :--- | :--- |
| `src/mdrap/vessel.py` | `tests/test_vessel*.py` (3 files) | None |
| `src/mdrap/options.py` | `tests/test_options*.py` (2 files) | None |
| `src/mdrap/fx.py` | `tests/test_fx.py` (1 file) | None |
| `src/mdrap/af_xdp.py` | `tests/test_af_xdp.py` (1 file) | None |
| `src/mdrap/navigator.py` | `tests/test_navigator*.py` (2 files) | None |
| `src/mdrap/terminal_display.py` | `tests/test_terminal_display*.py`, `test_terminal_tui.py` (3 files) | None |
| `src/mdrap/trading_cli.py` | `tests/test_cli_coverage.py` (partial CLI mock) | None |

---

## 3. Packaging & Entry-Point Inventory

### `pyproject.toml`
- Core dependencies: `[]` (Zero mandatory external dependencies).
- Wheel package find: `where = ["src"], include = ["mdrap", "mdrap.*"]`.
- Entry points:
  - `[project.scripts]`: `mdrap = "mdrap.cli:main"`.
  - `[project.entry-points."mdrap.adapters"]`: `template`, `reference`.
  - `[project.entry-points."mdrap.quality_rules"]`: `sample_rules`.
  - `[project.entry-points."mdrap.storage_backends"]`: `sample_memory`.
  - `[project.entry-points."mdrap.auth_providers"]`: `sample_auth`.
  - `[project.entry-points."mdrap.output_sinks"]`: `sample_sink`.
  - `[project.entry-points."mdrap.alert_sinks"]`: `sample_alert`.

All entry-points point exclusively to `mdrap.*`. Zero entry points reference `src/*.py` root shims.
