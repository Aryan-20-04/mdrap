# MDRAP Pilot Readiness Audit — Agent 6: Product Interface & Observability

**Agent Role:** Lead Auditor for CLI Interfaces, Operational Dashboards & API Endpoints  
**Target:** `cli.py`, `src/mdrap/cli/`, `src/mdrap/api.py`  
**Evaluation Standard:** Institutional Usability, Incident Observability & CLI Automation  
**Date:** 2026-11-11  

---

## 1. Executive Summary

Agent 6 audited the operator and automated interfaces for MDRAP, verifying how trading teams, reliability engineers, and compliance officers interact with the platform.

**Key Findings:**
1. **Operator Status Cockpit:** **VERIFIED**. `mdrap status` delivers immediate, comprehensive visibility into:
   - Segregated canonical vs quarantine event counts.
   - Per-venue watchdog states (Healthy, Degraded, Stale).
   - Real-time analytical stream coverage.
   - Active and historical Data Quality Incidents with forensic replay statuses.
2. **Deterministic Replay Interface:** **VERIFIED (With DB Routing Fix)**. `mdrap replay --incident <ID>` reconstructs the exact microstructure order book timeline and outputs a verifiable SHA-256 state hash.
3. **Structured JSON Output:** **VERIFIED**. Supplying `--json` to `status` and `replay` produces well-formatted JSON suitable for integration into Prometheus, Datadog, or downstream SIEM tools.
4. **Colorblind-Safe and Non-TTY Fallback:** **VERIFIED**. Standard library plain text fallbacks honor `NO_COLOR=1` and `--plain` flags cleanly.

---

## 2. CLI Interface Verification

### 2.1 Status Dashboard Output
Executed: `python cli.py status`
- Event Segregation:
  - Canonical Events: 56,466 (70.3% VALID, 29.7% SUSPICIOUS)
  - Quarantine: 16,913 (persisted for audit)
  - Lineage Traces: 55,626 (100% deterministic)
- Active Incident Display:
  - Displays incident `inc-1794334613-0001` (BYBIT, BTC/USD, SEQUENCE_GAP) with active badge and verified replay proof.

### 2.2 Replay CLI Invocation
Executed: `python cli.py replay --incident inc-1794334613-0001 --db data/mdrap.db`
- Reconstructs microsecond timeline table with reconstructed BBO, quality flags, and anomaly reasons.
- Displays sealed cryptographic replay state hash:
  `df34deb48b1770e7d35d81f3a822fbd25379630cf8a9976f5f5887b2dc6d8b79`

### 2.3 Identified Defect: Replay Parser Default Database
In `src/mdrap/cli/__init__.py:763`, the `replay` subcommand specifies `default_db="data/mdrap_replay.db"`. Because the active system database is `"data/mdrap.db"`, omitting the `--db` parameter causes an immediate database lookup failure.

---

## 3. Required Remediation for Pilot Readiness

1. **Intelligent DB Resolution for Incident Replay**:
   Update `cmd_replay` in `src/mdrap/cli/operations.py` so that when `--incident` is requested and the default `data/mdrap_replay.db` does not exist, it automatically checks `data/mdrap.db`.
