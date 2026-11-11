# MDRAP Codebase Consolidation — Deletion Register

This register documents every file, module, and command proposed for removal, deprecation, or relocation during the MDRAP product consolidation.

---

## Decision Categories
1. **DELETE**: Safe to remove completely. Code is dead, out of scope, a redundant shim, or a non-functional stub. All references must be migrated or removed without regressing core reliability tests.
2. **DEPRECATE**: Functionality is retained with a deprecation warning for one release cycle or retained as a compatibility redirect.
3. **MOVE TO OPTIONAL COMPONENT**: Isolate in optional extra or companion package without blocking core reliability runtime.
4. **RETAIN**: Critical to the market data reliability sidecar, provenance, replay, quality evaluation, or operational CLI.
5. **NEEDS MORE INVESTIGATION**: Ambiguous dependency requiring deeper verification before decision.

---

## 1. Root-Level Forwarding Shims (`src/*.py`)

All 93 shims are 5-line dynamic stubs (`import importlib; _sys.modules[__name__] = importlib.import_module('mdrap.<name>')`).
Packaging analysis confirms setuptools packages `mdrap.*` only; none of these 93 files are included in the distributed wheel.

| File | Disposition | Rationale | Internal References Checked | Verification |
| :--- | :--- | :--- | :--- | :--- |
| `src/alerts.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/alert_sinks.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/analytics.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/api.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/archive.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/async_fanout.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/audit_format.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/backtest.py` | DELETE | Forwarding shim | Referenced in old `market_latency_audit.py` (migrated to `mdrap.backtest`) | Benchmark import check |
| `src/bardb.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/bbo.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/benchmark.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/chaos.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/chd.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/chd_history.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/cli.py` | DELETE | Forwarding shim | Root `cli.py` at workspace root provides CLI entry point; `mdrap.cli` is the package. References in tests migrated to `from mdrap.cli import ...` | `pytest tests/test_cli.py` |
| `src/client.py` | DELETE | Forwarding shim | Referenced in `personas/02_intermediate_sdk.py` (migrated to `from mdrap import Client`) | Persona execution check |
| `src/clock.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/columnar.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/config.py` | DELETE | Forwarding shim | Referenced in `tools/generate_golden_vectors.py` (migrated to `from mdrap.config import ...`) | Tool execution check |
| `src/config_loader.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/consensus.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/dashboard.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/databento_feed.py`| DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/depth.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/engine.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/export.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/exporter.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/failover.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/fastpath.py` | DELETE | Forwarding shim | Referenced in benchmarks (migrated to `from mdrap import fastpath`) | Benchmark import check |
| `src/features.py` | DELETE | Forwarding shim | Referenced in `test_fastpath_quantitative.py` (migrated to `from mdrap import features`) | `pytest tests/test_fastpath_quantitative.py` |
| `src/feed_handler.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/fix_engine.py` | DELETE | Forwarding shim | Referenced in `test_fastpath_quantitative.py` (migrated to `from mdrap import fix_engine`) | `pytest tests/test_fastpath_quantitative.py` |
| `src/flow_tracker.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/fx.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/gateway.py` | DELETE | Forwarding shim | Referenced in benchmarks (migrated to `from mdrap import gateway`) | Benchmark import check |
| `src/gateway_tcp.py` | DELETE | Forwarding shim | Referenced in personas (migrated to `from mdrap import gateway_tcp`) | Persona import check |
| `src/historical.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/historical_verifier.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/incidents.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/ingestlog.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/ingress.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/itch.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/journal.py` | DELETE | Forwarding shim | Referenced in benchmarks (migrated to `from mdrap import journal`) | Benchmark import check |
| `src/kafka_sink.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/live.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/manifest.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/mbo.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/mdrap_build.py` | RETAIN | Build helper class | Used by `pyproject.toml` `build_py` | Wheel build |
| `src/metrics.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/models.py` | DELETE | Forwarding shim | Referenced in benchmarks (migrated to `from mdrap import models`) | Benchmark import check |
| `src/multicast_arbitrator.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/navigator.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/options.py` | DELETE | Forwarding shim | Referenced in `test_fastpath_quantitative.py` (migrated to `from mdrap import options`) | `pytest tests/test_fastpath_quantitative.py` |
| `src/partition.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/pcap.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/pipeline.py` | DELETE | Forwarding shim | Referenced in benchmarks (migrated to `from mdrap import pipeline`) | Benchmark import check |
| `src/plugins.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/polygon_feed.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/projection.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/prometheus.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/protocol.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/protocols.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/provenance.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/quality.py` | DELETE | Forwarding shim | Referenced in benchmarks (migrated to `from mdrap import quality`) | Benchmark import check |
| `src/quarantine.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/reconciliation.py`| DELETE | Forwarding shim | Referenced in benchmarks (migrated to `from mdrap import reconciliation`) | Benchmark import check |
| `src/recovery.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/replay.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/risk.py` | DELETE | Forwarding shim | Referenced in `test_fastpath_quantitative.py` (migrated to `from mdrap import risk`) | `pytest tests/test_fastpath_quantitative.py` |
| `src/rules.py` | DELETE | Forwarding shim | Referenced in `examples/custom_venue` (migrated to `from mdrap import rules`) | Example check |
| `src/runtime.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/sbe.py` | DELETE | Forwarding shim | Referenced in benchmarks (migrated to `from mdrap import sbe`) | Benchmark import check |
| `src/scheduler.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/security.py` | DELETE | Forwarding shim | Referenced in scripts (migrated to `from mdrap import security`) | Script check |
| `src/service.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/shm.py` | DELETE | Forwarding shim | Referenced in benchmarks (migrated to `from mdrap import shm`) | Benchmark import check |
| `src/shm_drainer.py` | DELETE | Forwarding shim | Referenced in benchmarks (migrated to `from mdrap import shm_drainer`) | Benchmark import check |
| `src/simulator.py` | DELETE | Forwarding shim | Referenced in benchmarks (migrated to `from mdrap import simulator`) | Benchmark import check |
| `src/storage.py` | DELETE | Forwarding shim | Referenced in benchmarks (migrated to `from mdrap import storage`) | Benchmark import check |
| `src/strategy_sdk.py` | DELETE | Forwarding shim | Referenced in examples (migrated to `from mdrap import strategy_sdk`) | Example check |
| `src/stresstest.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/supervisor.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/symbology.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/tca.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/term.py` | DELETE | Forwarding shim | Referenced in benchmarks (migrated to `from mdrap import term`) | Benchmark check |
| `src/terminal_display.py`| DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/trading_cli.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/venues.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/vessel.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/watchdog.py` | DELETE | Forwarding shim | Referenced in benchmarks (migrated to `from mdrap import watchdog`) | Benchmark check |
| `src/workload_simulator.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/ws_feed.py` | DELETE | Unused forwarding shim | 0 references | `pytest tests/test_product_assurance.py` |
| `src/_version.py` | DELETE | Forwarding shim | Referenced in `test_api_endpoints.py`, `test_docs_verification.py`, `test_sdk_commercial.py` (migrated to `from mdrap._version import __version__`) | `pytest tests/test_api_endpoints.py` |
| `src/__init__.py` | RETAIN (CLEANED) | Top-level package init | Updated to import cleanly from `mdrap.*` | Package import test |

---

## 2. Bloat & Out-of-Scope Modules in `src/mdrap/`

| Module | Lines | Disposition | Rationale | References & Impact |
| :--- | :--- | :--- | :--- | :--- |
| `src/mdrap/navigator.py` | 1,234 | DELETE | Excel/Vim style modal terminal trading desk. Irrelevant to headless reliability sidecar. | Used by `cmd_desk` in `cli/integrations.py` and tested by `test_navigator*.py`. CLI entry point removed. |
| `src/mdrap/terminal_display.py` | 1,238 | DELETE | Terminal sparklines, ANSI candlestick charts, curses event loop. Out of scope for reliability engine. | Used by `cmd_chart` and tested by `test_terminal_display*.py`. Replaced with Rich table/text telemetry. |
| `src/mdrap/trading_cli.py` | 776 | DELETE | Interactive trading desk, backtest CLI, portfolio tracking, and order simulator. Directly contradicts read-only assurance mission. | Registered via `add_trading_parsers`. Subparsers removed from CLI. Zero core reliability dependency. |
| `src/mdrap/tui.py` | 236 | DELETE | Standalone interactive TUI application loop. Out of scope. | Registered as `cmd_tui`. Removed from CLI dispatch. |
| `src/mdrap/vessel.py` | 78 | DELETE | Deprecated AIS maritime intelligence tracking stub. | Deprecated since v5.3.0 in favor of `packages/mdrap-contrib-vessel`. Zero core engine usage. CLI command `cmd_vessel` removed. |
| `src/mdrap/options.py` | 105 | DELETE | Deprecated derivatives pricing / Greeks stub. | Deprecated since v5.3.0 in favor of `packages/mdrap-options`. Zero core engine usage. |
| `src/mdrap/fx.py` | 116 | DELETE | Deprecated hardcoded FX triangular arbitrage matrix. | Deprecated stub. Venue currencies are handled by self-contained `venues.py`. |
| `src/mdrap/af_xdp.py` | 227 | DELETE | Incomplete AF_XDP kernel-bypass stub without native Linux driver or C bindings. | Zero usages in `src/mdrap/`. Only mocked in `test_af_xdp.py`. |
| `src/mdrap/cli/integrations.py` (`cmd_edgar`) | ~30 | DELETE | SEC corporate filings research mock stub. | Empty mock printing a deprecation notice. Removed. |
| `src/mdrap/cli/integrations.py` (`cmd_vessel`) | ~250 | DELETE | Maritime vessel tracker CLI. | Out of scope. Removed. |

---

## 3. Infrastructure & Transport Subsystems

| Module | Disposition | Rationale | Technical Boundary |
| :--- | :--- | :--- | :--- |
| `src/mdrap/kafka_sink.py` | MOVE TO OPTIONAL COMPONENT | Checkpointed background SQLite WAL tailer publishing to Kafka / Redpanda. Provides distributed event delivery (distinct from SQLite WAL local recovery). | Isolate under optional extra `[kafka]`. Pure Python fallback with `InMemoryKafkaProducer` preserved. |
| `src/mdrap/pcap.py` | RETAIN AS DIAGNOSTIC HARNESS | Authentic standard Libpcap 2.4 / MoldUDP64 multicast packet dissector and pacer. Essential for wire-protocol fuzzing and packet-level network incident diagnostics. | Diagnostic parser tool, not hooked into hot tick path. Verified by `test_phase21_feed_recovery_and_pcap.py`. |
| `src/mdrap/term.py` | RETAIN (STREAMLINED) | Headless-safe abstraction wrapping `rich` with stdlib plain-text fallback. Ensures `--no-color`, non-TTY, piped output, and CI logs function without ANSI corruption. | Verified by `test_term_stdlib_fallback_renders_clean_text`. |

---

## 4. Dependencies & Configuration

| Component | Current State | Proposed Disposition | Migration Path |
| :--- | :--- | :--- | :--- |
| `pyyaml` | In `requirements.txt` | Remove from core requirements; move to optional extra `[yaml]` | Support `.toml` via standard library `tomllib` (Python >= 3.11) and `.json` via `json`. Retain backward-compatible regex/line parser for legacy `.yaml` files in `config.py`. |
| `duckdb` | In `requirements.txt` | Move to optional extra `[columnar]` | SQLite is the canonical zero-dependency persistence tier. DuckDB is loaded conditionally only when OLAP analytics are invoked. |
| `pyarrow` | In `requirements.txt` | Move to optional extra `[export]` | Parquet export is loaded conditionally; core operates without PyArrow. |
| `rich` | In `requirements.txt` | Retain in core CLI / UI | Zero hard crash risk due to `term.py` stdlib fallback. High-value telemetry rendering for operational reliability commands. |
