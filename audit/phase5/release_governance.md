# MDRAP Phase 5 — Release Governance and Change Management Policy

## 1. Executive Summary & Policy Objective
This document defines the formal change management and release governance framework for the Market Data Reliability & Acceleration Platform (MDRAP). Because MDRAP processes institutional market data upstream of trading execution engines, quantitative analytics, and regulatory reporting ledgers, all software, schema, configuration, and infrastructure changes must satisfy strict, non-negotiable verification gates prior to deployment into production environments.

---

## 2. Release Versioning & SemVer Specification
MDRAP strictly adheres to **Semantic Versioning 2.0.0** (`MAJOR.MINOR.PATCH`) with formalized institutional pre-release identifiers:

- **MAJOR (`X.0.0`)**: Incompatible API breaks, SBE schema version changes requiring consumer upgrades, persistence schema breaking changes, or behavioral changes to the core correctness contracts.
- **MINOR (`x.Y.0`)**: Backward-compatible new functionality (e.g., new venue adapter, new Prometheus telemetry metrics, non-breaking CLI subcommands).
- **PATCH (`x.y.Z`)**: Backward-compatible bug fixes, performance optimizations preserving binary parity, and non-breaking security patches.
- **Pilot & Pre-release Tags**:
  - `vX.Y.Z-rc.N`: Release Candidate under Phase 4 verification.
  - `vX.Y.Z-pilot.N`: Controlled pilot build authorized strictly for designated pilot nodes under Profile A.

---

## 3. Governance Roles and Authorization Matrix

A release requires explicit cryptographic or formal multi-party sign-offs across four functional domains:

| Domain | Authorized Role | Approval Criteria |
| :--- | :--- | :--- |
| **Market Data Architecture** | Principal Architect | Adherence to single-source-of-truth invariants, deterministic ordering, and SBE wire contracts. |
| **Site Reliability / Ops** | Principal SRE / Ops Lead | Runbook completeness, Prometheus/Grafana dashboard coverage, and zero regression in deployment automation. |
| **Security & Compliance** | Security Architect | Zero high/critical static/dynamic vulnerabilities, secret audit clean, zero unauthenticated endpoints. |
| **Quantitative / Trading** | Desk Integration Lead | Independent consumer validation passed, latency SLOs preserved (p99 < 500 µs), no data corruption. |

---

## 4. Release Verification Gates

Before any build is promoted to `vX.Y.Z-pilot` or `vX.Y.Z-ga`, it must automatically satisfy the following sequential verification gates:

```
[ Gate 1: Code & Lint ] ──> [ Gate 2: Full Test Suite ] ──> [ Gate 3: Chaos & Fault ]
                                                                       │
[ Gate 6: Security Audit ] <── [ Gate 5: 25k Soak/Perf ] <── [ Gate 4: Zero-Loss Proof ]
            │
            └──> [ Gate 7: Multi-Party Approval ] ──> [ Staged Deployment ]
```

### Gate 1: Static Code and Linting Gate
- Clean pass of Ruff / flake8 linting.
- MyPy / strict type checking on all public APIs.
- Zero undocumented deprecated dependencies.

### Gate 2: Automated Test Coverage Gate
- Full pytest regression suite execution across all test suites (`tests/test_*.py`).
- Pure Python fallback mode verification (`$env:MDRAP_DISABLE_FASTPATH="1"`).
- Native C fastpath compilation and validation (where supported).
- Minimum code branch coverage of 85% across core modules (`src/gateway.py`, `src/quality.py`, `src/journal.py`, `src/reconciliation.py`).

### Gate 3: Resilience & Chaos Gate
- Automated fault injection (`src/chaos.py`) must demonstrate graceful degradation:
  - Synthetic socket disconnection triggers automatic reconnection without process crash.
  - Disk backpressure triggers bounded queuing and explicit backpressure logging.
  - Zero unhandled exceptions or thread panics.

### Gate 4: Zero-Loss and Deterministic Parity Gate
- End-to-end replay test verifying identical canonical state reconstruction from identical raw event sequences (`seed=42`).
- Monotonic sequence verification: zero gaps, zero duplicates, zero out-of-order events emitted without explicit `SUSPICIOUS` flags.

### Gate 5: Performance and Soak Gate
- 25,000+ event pilot soak test benchmark (`benchmarks/phase5_benchmark.py`).
- Throughput must exceed SLO baseline (> 3,000 eps for single-threaded ingest with grouped fsync).
- Tail latency p99 must remain under 500 µs.
- Memory leak detection: RSS delta must remain under 5 MB over 25,000 events.

### Gate 6: Security and Vulnerability Gate
- Automated secret scanning (zero committed API tokens or credentials).
- Vulnerability scan across Python dependencies (`pip-audit` / CodeQL).
- Verification that all operational diagnostic exports (`scripts/diagnostic_bundle.py`) redact credentials.

---

## 5. Emergency Change Exception (Hotfix Protocol)
Under active SEV-1 production incidents, an expedited hotfix path is permitted with strict post-facto audit:
1. Incident Commander and Lead Architect grant verbal/signed emergency authorization.
2. Hotfix branch `hotfix/vX.Y.Z` created from the deployed tag.
3. Surgical fix committed; minimal regression test added.
4. Gate 1, Gate 2, and Gate 4 must pass locally (no skipped tests).
5. Hotfix deployed to pilot node with active monitoring.
6. Retrospective PIR and standard release documentation completed within 24 hours.
