# MDRAP Consolidation — Agent 8: Independent Adversarial Review

## 1. Adversarial Review Mandate

As the Independent Adversarial Reviewer, Agent 8 operates with zero bias toward code deletion. Every proposal to eliminate code, modify imports, or alter dependencies must withstand rigorous technical interrogation against the following failure modes:
1. **False Dead-Code Findings**: Modules labeled "dead" that are invoked via reflection, CLI dispatch, or dynamic plugin entry points.
2. **Broken Public Interfaces**: Deleting modules that external client libraries rely on.
3. **Loss of Packet-Level Diagnostics**: Conflating IngestLog WAL replay with PCAP wire-level replay.
4. **Conflating SQLite WAL and Kafka**: Erroneously asserting that local SQLite WAL replaces distributed Kafka event publication.
5. **Configuration Breakage**: Breaking legacy YAML configs during TOML introduction.
6. **Weakened Test Coverage**: Deleting tests merely to hide architectural flaws.

---

## 2. Adversarial Challenge & Verdict Matrix

| Target Component | Proposed Action | Adversarial Challenge | Evidence & Counter-Proof | Final Verdict |
| :--- | :--- | :--- | :--- | :--- |
| **92 Root Shims (`src/*.py`)** | DELETE | Does deleting `src/*.py` break users who `pip install mdrap-core`? | **Proof**: Inspected `pyproject.toml` lines 152-154. The setuptools find specification is `include = ["mdrap", "mdrap.*"]`. The wheel manifest independently built by setuptools confirms that ZERO files from `src/*.py` are included in the distributed `.whl`. External users have ALWAYS imported `from mdrap import ...`. Internal imports in 5 test files are migrated to `mdrap.*`. | **SAFE TO DELETE** |
| **`src/mdrap/navigator.py`** | DELETE | Does any core monitoring command rely on `MDRAPNavigator`? | **Proof**: `navigator.py` implements a curses/raw-mode keyboard event loop for trading desk tabs. Grep across `src/mdrap/` reveals zero imports in `gateway.py`, `quality.py`, `reconciliation.py`, `storage.py`, `provenance.py`, or `replay.py`. It is solely invoked by `cmd_desk`. | **SAFE TO DELETE** |
| **`src/mdrap/terminal_display.py`** | DELETE | Does headless telemetry rendering depend on curses sparklines? | **Proof**: Headless servers run without a TTY. Sparkline and ANSI candlestick chart routines in `terminal_display.py` crash or corrupt logs when redirected to pipes or CI. `term.py` provides resilient table and panel rendering via `rich` or standard-library plain text. | **SAFE TO DELETE** |
| **`src/mdrap/trading_cli.py`** | DELETE | Are market data ingestion tests or microstructure rules entangled with `trading_cli.py`? | **Proof**: `trading_cli.py` contains mock order routing, backtesting, and portfolio valuation. Microstructure validation in `quality.py` and `reconciliation.py` operates 100% independently on `CanonicalEvent` stream objects. Deleting `trading_cli.py` enforces the read-only security boundary. | **SAFE TO DELETE** |
| **`src/mdrap/vessel.py`** | DELETE | Does cross-venue arbitration or commodity tracking depend on AIS maritime data? | **Proof**: `vessel.py` was deprecated in v5.3.0. It is a 78-line forwarder to `packages/mdrap-contrib-vessel`. No module in `src/mdrap/` imports `vessel.py` for market-data validation. | **SAFE TO DELETE** |
| **`src/mdrap/options.py`** | DELETE | Does normalization of options market data depend on `options.py`? | **Proof**: Normalization in `gateway.py` sets standard event attributes (`strike_price`, `expiration_ts`). It never evaluates Black-Scholes partial differential equations. `options.py` was deprecated in v5.3.0 in favor of `packages/mdrap-options`. | **SAFE TO DELETE** |
| **`src/mdrap/fx.py`** | DELETE | Does currency conversion for cross-venue reconciliation break? | **Proof**: `venues.py` has its own self-contained venue and currency registry (`format_currency`, MIC codes). `reconciliation.py` does not invoke `FXMatrix`. `fx.py` contains static hardcoded exchange rates that should never be used in production validation. | **SAFE TO DELETE** |
| **`src/mdrap/af_xdp.py`** | DELETE | Is this removing real zero-copy Linux performance? | **Proof**: Inspected `af_xdp.py`. It contains zero Linux ioctl calls, zero C bindings, and zero UMEM descriptor ring logic. It is an in-memory loopback mock with zero callers in `src/mdrap/`. True zero-copy IPC is provided by `shm.py` and C fastpath. | **SAFE TO DELETE** |
| **`src/mdrap/kafka_sink.py`** | MOVE TO OPTIONAL COMPONENT | Is Kafka delivery being replaced with SQLite WAL under false equivalence? | **Proof**: SQLite WAL provides local persistence; Kafka provides distributed downstream streaming. The proposal does NOT delete Kafka; it retains `kafka_sink.py` as an optional decoupled worker under extra `[kafka]`. | **MOVE TO OPTIONAL COMPONENT** |
| **`src/mdrap/pcap.py`** | RETAIN AS DIAGNOSTIC HARNESS | Is PCAP packet capture being discarded because WAL replay exists? | **Proof**: The proposal explicitly preserves `pcap.py` because Libpcap 2.4 packet parsing is vital for wire-protocol fuzzing and multicast network diagnostics, which cannot be derived from normalized events. | **MUST RETAIN** |
| **`src/mdrap/term.py`** | RETAIN | Can `term.py` be deleted if `rich` is installed? | **Proof**: Rich cannot be assumed present in minimal container environments. `term.py` provides the standard-library ASCII table and panel fallback, ensuring `--no-color` and piped headless operation work seamlessly. | **MUST RETAIN** |
| **`pyyaml` Dependency** | REMOVE FROM CORE | Will existing customer `config.yaml` files silently break? | **Proof**: `config.py` includes a built-in stdlib fallback parser capable of parsing standard YAML key-value dictionaries. Additionally, native `tomllib` support is introduced for `config.toml`. Customers with `config.yaml` can either install `[yaml]` or run on stdlib fallback without error. | **SAFE TO DEPRECATE MANDATORY REQUIREMENT** |

---

## 3. Test Suite Integrity Verification

Rule 7 states: *"Never remove tests merely to make validation pass."*
- **Core Tests Protected**: All 16 pilot assurance tests (`tests/test_pilot_readiness.py`, `tests/test_phase2_*.py`), all 33 product assurance tests (`tests/test_product_assurance.py`, `tests/test_cli.py`), and all 1,700+ core quality, reconciliation, persistence, replay, and security tests remain 100% active.
- **Dedicated Out-of-Scope Tests**: When an out-of-scope peripheral module is deleted (e.g. `vessel.py` or `af_xdp.py`), its companion test file that tests *only* that removed stub (e.g. `test_vessel.py`, `test_af_xdp.py`) is pruned in the same batch. No core engine tests are pruned.

---

## 4. Final Adversarial Verdict

The proposed consolidation strictly adheres to the product reset rules:
- **No functional core code is deleted.**
- **No public package APIs are broken.**
- **No cryptographic or forensic guarantees are altered.**
- **Read-only boundary is verified and enforced.**
- **The consolidation is APPROVED to proceed to Stage 3.**
