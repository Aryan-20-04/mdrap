# MDRAP Phase 5 — Staged Rollout and Canary Deployment Plan

## 1. Overview and Rollout Philosophy
The introduction of MDRAP into institutional production follows a progressive, evidence-gated staged rollout model. Changes are never introduced directly to all consumers simultaneously. Each stage represents an isolated operational boundary with defined traffic levels, monitoring durations, and automated rollback triggers.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ STAGE 0: Pre-Flight Lint & Dry-Run (Sandbox / Offline)                       │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ STAGE 1: Shadow Traffic Deployment (Passive Validation, 0 Live Consumers)    │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ STAGE 2: Canary Consumer Deployment (Single Internal Analytics Consumer)     │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ STAGE 3: Controlled Pilot Production (Designated Desk Consumers, Profile A)  │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ STAGE 4: General Institutional Production (Multi-Node HA, Full Expansion)    │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Stage Breakdown & Progression Gates

### Stage 0: Pre-Flight Verification & Sandbox Dry-Run
- **Objective**: Ensure host readiness, dependency isolation, schema compatibility, and configuration validity.
- **Traffic**: 0% real or replay traffic.
- **Duration**: Pre-deployment (< 10 minutes).
- **Execution**:
  ```bash
  python scripts/deploy_pilot.py --check-only --config config/pilot_profile_a.json
  ```
- **Advancement Gate**:
  - `scripts/deploy_pilot.py` exits with status `0`.
  - System prerequisites satisfied (Python 3.11+, memory > 8 GB, disk free > 50 GB).
  - Config hash matches approved release manifest.

### Stage 1: Shadow Mode (Passive Validation)
- **Objective**: Ingest live or deterministic replay feed traffic without routing output to production consumers. Evaluate quality checks, deduplication, IngestLog I/O, and reconciler stability against legacy feed baselines.
- **Traffic**: 100% incoming data ingest; 0 live downstream consumer subscriptions.
- **Duration**: Minimum 4 hours continuous operation (or full trading session replay).
- **Validation Actions**:
  - Compare canonical tick output with legacy feed ticks.
  - Audit Merkle tree generation and SQLite WAL checkpoint timing.
  - Verify zero unhandled exceptions in logs.
- **Advancement Gate**:
  - 0 unhandled exceptions.
  - Zero memory growth over 4 hours (steady-state RSS).
  - Reconciler disagreement rate < 0.05%.

### Stage 2: Canary Consumer Deployment
- **Objective**: Connect a single non-critical downstream consumer (e.g., Risk Analytics or Offline TCA Engine) via native SBE socket.
- **Traffic**: 100% feed ingest; 1 canary consumer actively decoding canonical ticks.
- **Duration**: Minimum 2 hours.
- **Validation Actions**:
  - Verify consumer heartbeat and monotonic sequence receipt.
  - Inspect consumer latency histograms (ensure p99 < 500 µs).
  - Verify entitlement metering records accurate consumer tick consumption.
- **Advancement Gate**:
  - Canary consumer reports 0 sequence gaps.
  - Licensing audit counter matches emitted event count exactly.
  - SLO dashboard indicates green health across all monitors.

### Stage 3: Controlled Pilot Production
- **Objective**: Authorize registered pilot desk consumers (Algorithmic Execution Sandbox, Real-Time Portfolio Pricing).
- **Traffic**: Full production pilot volume (Profile A, up to 10,000 eps burst).
- **Duration**: Minimum 2 continuous weeks of trading days under active monitoring.
- **Operating Constraints**:
  - Strict adherence to Profile A limits (single host, local SSD WAL, local SBE socket).
  - 24/7 SRE on-call coverage with automated alerting.
- **Advancement Gate**:
  - Continuous 100% availability during market hours.
  - Zero SEV-1 or SEV-2 incidents.
  - Compliance and usage metering logs verified and signed off by Operations.

### Stage 4: Multi-Node Institutional Expansion
- **Objective**: Transition to distributed high-availability topology (Active-Passive failover, cross-region replication).
- **Traffic**: Global production market data feeds.
- **Advancement Gate**:
  - Explicit sign-off under the **Phase 5 Expansion Gate** (`audit/phase5/expansion_gate.md`).
  - Independent security penetration test completion.
  - Multi-site automated failover drills verified under production traffic.

---

## 3. Automated Abort & Pause Triggers

If any of the following conditions occur during Stage 1, 2, or 3, rollout is **immediately paused and reverted**:

1. **Sequence Discontinuity**: Any consumer reports a sequence gap > 0 that cannot be reconciled within 100 ms.
2. **Crash / Panic**: Any uncaught Python exception or native core crash.
3. **Data Invalidation Spike**: Quality rule invalidation rate exceeds 2.0% of incoming volume.
4. **Latency SLO Breach**: Ingest-to-consumer p99 latency exceeds 1,000 µs for > 30 consecutive seconds.
5. **Disk Backpressure**: IngestLog queue depth exceeds 50,000 events or disk partition utilization exceeds 85%.
