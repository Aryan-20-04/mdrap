# MDRAP Consolidation — Agent 3: Terminal Rendering & CLI Consolidation

## 1. Executive Summary

Agent 3 audited the terminal rendering architecture, command dispatch, and CLI interfaces of MDRAP.

The previous codebase suffered from severe identity confusion:
- It included a 1,234-line Excel/Vim style modal terminal trading desk (`navigator.py`).
- It included a 1,238-line curses sparkline and candlestick rendering loop (`terminal_display.py`).
- It included a 236-line Textual TUI loop (`tui.py`).
- It included 776 lines of retail order simulation (`trading_cli.py`).

Together, these four modules added **3,484 lines of bloated interactive UI code** to what is fundamentally an institutional, headless market-data reliability sidecar running on Linux/Windows servers.

---

## 2. Headless Operational CLI Target

The consolidated MDRAP CLI focuses strictly on operational observability, evidence verification, and incident diagnosis:

| Command | Action & Purpose | Headless / Non-TTY Compatible |
| :--- | :--- | :--- |
| `mdrap status` | Ingest health, connector status, WAL offset, buffer watermarks | Yes (Rich Table / Stdlib Fallback / `--json`) |
| `mdrap version` | Engine version, build hash, native C acceleration status | Yes (Plain text / `--json`) |
| `mdrap doctor` | Environment diagnostics, SHM availability, seqlock benchmarks | Yes (Rich Table / Plain text) |
| `mdrap replay` | Deterministic incident replay for historical anomalies | Yes (Progress / Batch output) |
| `mdrap incident` | List, query, and inspect data-quality anomalies | Yes (Rich Table / `--json`) |
| `mdrap audit` | Merkle audit tree root calculation and leaf count | Yes (Cryptographic hash output) |
| `mdrap verify` | Standalone evidence verification against RFC 6962 tree | Yes (PASS/FAIL exit codes) |
| `mdrap security` | Zero-trust AST scan and read-only API enforcement check | Yes (Security verdict table) |
| `mdrap chaos` | Connector chaos drills (bursts, gaps, clock jumps) | Yes (Batch drill transcript) |
| `mdrap export` | Forensic evidence export to JSON/Parquet | Yes (Non-interactive file write) |

All interactive modal keyboard loops (`nav`, `desk`, `chart`, `ticker`, `tui`) are removed from the core CLI.

---

## 3. Rendering Architecture: Rich with Stdlib Fallback

### Design Decision
Rather than writing and maintaining a competing custom rendering engine, MDRAP standardizes on:
1. **`rich` for formatting**:
   - Clean tables with explicit column widths.
   - Status panels.
   - Structured JSON printing.
   - Colorblind-safe telemetry palettes.
2. **`src/mdrap/term.py` as the Resilient Bridge**:
   - `term.py` wraps `rich.console.Console`, `rich.table.Table`, and `rich.panel.Panel`.
   - When `rich` is missing, or when output is redirected to a pipe/file, or when `--no-color` / `NO_COLOR=1` is set, `term.py` automatically falls back to standard-library plain ASCII text formatting.
   - This ensures zero ANSI escape corruption in exported log files or CI pipelines.

### Verified Headless Guarantees
- `--no-color` flag: Strips all ANSI escape codes.
- Piped output (`mdrap status | grep ...` or `mdrap incident list --json | jq ...`): Clean machine-readable output without terminal control characters.
- Non-TTY execution: Background daemons and systemd services run without allocating a pseudo-terminal (pty).
- Error messages: Rendered cleanly to `stderr` without requiring terminal styling.

---

## 4. Pruning Actions

1. **Delete**:
   - `src/mdrap/navigator.py` (1,234 lines)
   - `src/mdrap/terminal_display.py` (1,238 lines)
   - `src/mdrap/tui.py` (236 lines)
   - `src/mdrap/trading_cli.py` (776 lines)
2. **Clean in `src/mdrap/cli/`**:
   - Remove subparsers `p_desk`, `p_tui`, `p_chart`, `p_ticker`, `add_trading_parsers` from `src/mdrap/cli/__init__.py`.
   - Remove dead mnemonic mappings (`desk`, `navigator`, `nav`, `chart`, `ticker`).
   - Retain all core operational commands and tests.
