# CLI Reference Manual

MDRAP provides an institutional terminal command-line interface via `cli.py` (or `python -m mdrap.cli`).

---

## 1. Global Flags

| Flag | Description |
| :--- | :--- |
| `--config <path>` | Path to TOML configuration file (defaults to `mdrap.toml` or `deploy/config.toml`) |
| `--json` | Output machine-readable structured JSON instead of formatted tables |
| `--no-color` / `--plain` | Suppress ANSI escape sequences and terminal colors (honors `NO_COLOR=1`) |
| `-v` / `--verbose` | Increase logging verbosity to `DEBUG` |

---

## 2. Command Reference

### `doctor`
Performs an automated diagnostic scan of runtime environment, native C compiler, vectorized engine tiers, QPC high-resolution timers, and SQLite WAL storage.
```bash
python cli.py doctor
```

### `status`
Displays a real-time system overview including active channels, memory usage, throughput rates, and feed health indicators.
```bash
python cli.py status
```

### `live [SYMBOL]`
Launches in-place live observation of public market data feeds (e.g. Coinbase, Kraken) without executing trades.
```bash
python cli.py live BTC-USD
```

### `health`
Inspects connector reputation, clock drift, sequence gap history, and transport latency per venue.
```bash
python cli.py health
```

### `incident [list|show <id>]`
Inspects recorded data quality incidents, anomaly types, and trigger event corridors.
```bash
# List all incidents
python cli.py incident list

# Show detailed incident metadata
python cli.py incident show inc-1700000001-0001
```

### `replay --incident <id> [--buffer <seconds>]`
Executes deterministic replay across an incident corridor under an isolated `VirtualClock`.
```bash
python cli.py replay --incident inc-1700000001-0001 --buffer 5.0
```

### `audit`
Inspects the RFC 6962 Merkle audit log and verifies root hash anchors.
```bash
python cli.py audit
```

### `verify <evidence_file.json>`
Executes standalone cryptographic verification of an exported forensic evidence manifest.
```bash
python tools/verify_evidence.py demo/output/evidence_inc-1700000001-0001.json
```

### `chaos [drill_name]`
Executes controlled connector conformance and chaos drills (e.g. sequence gaps, network disconnects, corrupted CRC frames) to verify resilience.
```bash
python cli.py chaos sequence_gap
```

### `sec`
Inspects security configuration, salt strength, read-only boundary enforcement, and API access rules.
```bash
python cli.py sec
```

### `daemon`
Launches the long-running streaming assurance sidecar daemon.
```bash
python cli.py daemon --config deploy/config.toml
```
