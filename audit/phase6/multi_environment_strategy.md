# MDRAP Phase 6 — Multi-Environment Deployment & Promotion Strategy

## 1. Executive Summary & Architecture
To ensure high availability, deterministic operational behavior, and zero configuration drift across heterogeneous deployments, MDRAP defines a standardized **Multi-Environment Operating Model**.

Each environment enforces strict boundaries, explicit capability profiles, and automated promotion gates to prevent unvalidated changes from impacting production trading desks.

---

## 2. Environment Matrix & Operational Profiles

| Dimension | DEV (Local Dev) | CI (Continuous Integration) | STAGING / UAT | DR (Disaster Recovery) | PROD (Production Cluster) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Topology** | Single-node standalone | Single-node ephemeral | 2-Shard partitioned cluster | 2-Shard warm standby | Multi-shard partitioned cluster |
| **Ingress Feeds** | Synthetic simulator (`src/simulator.py`) | Deterministic binary replay | Recorded PCAP / ITCH exchange feeds | Async WAL follower stream | Co-located optical cross-connects |
| **Storage Medium** | Local temp SQLite | In-memory / temp SQLite | Dedicated SSD | Replicated NVMe volume | Enterprise NVMe SSD (WAL mode) |
| **IPC / Transport** | Local loopback TCP | Local loopback TCP | Unix Domain Sockets / SHM | Cold replication stream | Lock-free SHM + Kernel-bypass TCP |
| **Security Mode** | Permissive / local test keys | Ephemeral test secrets | Scrubbed production tokens | Production keys (dormant) | Production HSM / salted PBKDF2 |
| **Test Scope** | Feature iteration | Full unit + regression (1,212 tests) | 24-hour soak + failover drills | Failover promotion verification | Live trading traffic |

---

## 3. Configuration Governance & Drift Prevention
1. **Immutable Configuration Templates**: All environments derive configuration from base templates stored in version control (`config/base.json`).
2. **Environment Variable Injection**: Environment-specific overrides (listen ports, socket paths, database URIs) are injected via strict environment variables (`MDRAP_ENV`, `MDRAP_SHARD_ID`, `MDRAP_DATA_DIR`).
3. **Drift Detection**: The startup health check runs a cryptographic SHA-256 hash validation over the configuration object, logging the config digest to the audit trail.
4. **Zero Production Secrets in Repo**: All production secrets are provisioned at runtime via environment variables or secret vaults.

---

## 4. Promotion Gate Workflow

```
[ Developer Code ] ──> [ CI Gate: 1,212 Tests Pass (< 5 min) ]
                                      │
                                      ▼
                       [ Benchmark Gate: eps ≥ 15,000, p99 ≤ 50 µs ]
                                      │
                                      ▼
                       [ STAGING: 4-Hour Soak + Fault Drills ]
                                      │
                                      ▼
                       [ Management Signoff & EOD Maintenance Window ]
                                      │
                                      ▼
                       [ PROD: Shard-by-Shard Rolling Deployment ]
```

### Staging Verification Criteria
Before promoting a release candidate to Production:
- **Soak Duration**: 4 hours uninterrupted execution under $\ge 10,000\text{ eps}$ synthetic traffic.
- **Error Budget**: Zero unhandled exceptions; zero corrupted WAL records.
- **Failover Verification**: Drill execution of `FAIL-SHARD-KILL` demonstrating recovery in $< 2.0\text{ seconds}$.
