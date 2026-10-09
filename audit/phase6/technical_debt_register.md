# MDRAP Phase 6 — Technical Debt Register & Maintenance Roadmap

## 1. Executive Summary & Debt Philosophy
In accordance with the `/ponytail` philosophy, technical debt is not a moral failing—it is a conscious, recorded trade-off made to deliver clean, working solutions without speculative over-engineering. This register tracks all known debt items, their operational impact, remediation cost, and scheduled timing.

---

## 2. Prioritized Technical Debt Register

| Debt ID | Subsystem | Issue Description | Operational Impact | Remediation Cost | Scheduled Release |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **DEBT-01** | Non-Core Modules | Peripheral analytics (`strategy_sdk.py`, `vessel.py`, `tca.py`, `terminal_display.py`) remain in tree emitting deprecation warnings. | Minor clutter; increases test collection time slightly. | Low (Excise to external consumer package) | Phase 7 / `v2.0.0` |
| **DEBT-02** | Package Layout | Duplicate root layout (`src/*.py` vs `src/mdrap/*.py`) creates import path quirks. | Minor developer confusion; mitigated by alias modules. | Medium (Consolidate into single namespace) | Phase 7 |
| **DEBT-03** | Network Sockets | Standard blocking thread-per-client TCP sockets limit maximum consumer fanout to ~30 clients. | Acceptable for current 10-client desk needs; bottlenecks at 50+ clients. | Medium (Implement asyncio / epoll event loop) | Phase 7 |
| **DEBT-04** | Distributed HA | Active-passive replication lacks automated Raft distributed fencing for zero-touch multi-site failover. | Manual or script-driven DR failover required (< 5 min). | High (Implement dedicated Raft consensus module) | Phase 7 (Post-Expansion Gate) |

---

## 3. Remediation Directives
- **Zero Refactoring for Aesthetics**: Code refactoring is undertaken solely when tied to measurable throughput gains, verified bug fixes, or deprecation retirements.
