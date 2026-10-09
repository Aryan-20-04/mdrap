# MDRAP Phase 4 — Production Readiness Contract

**Document Identifier**: `MDRAP-CONTRACT-P4-001`  
**Date**: October 9, 2026  
**Status**: APPROVED SPECIFICATION  

---

## 1. Supported Deployment Profiles

### Profile A: Single-Node Core Deployment
- **Ingress**: TCP Sockets, WebSockets, Replay Adapters
- **Engine**: IngestLog WAL + SQLite Projection + Native C Fastpath
- **Distribution**: Win32/POSIX Shared Memory IPC + WebSocket API
- **Persistence**: Local SSD with WAL mode and periodic checkpointing
- **Target Workload**: Quantitative trading execution nodes, colocation feeds, backtesting replay

### Profile B: Active-Passive High Availability
- **Primary & Standby Nodes**: 2 nodes in same datacenter / cluster
- **Coordination**: Heartbeat exchange (default interval 1.0s, timeout 2.0s)
- **Fencing**: Monotonic Epoch Tokens (`epoch: int`); writer rejected with `StaleEpochError`
- **Replication**: Semi-synchronous WAL frame replication over TCP
- **Sequence Continuity**: Standby blocks auto-promotion (`SYNCING` state) until sequence caught up

---

## 2. Measurable Service Level Objectives (SLOs)

| Objective | Target | Metric / Scope | Validation Procedure |
|---|---|---|---|
| **Data Integrity** | 100% | Zero undetected frame corruption or silent drops | CRC32 frame checksums & quarantine verification |
| **Sequence Continuity**| Monotonic | Zero sequence regressions per `(venue, feed_id)` | Sequence domain audit in `BaseFeedAdapter` |
| **Ingress Throughput** | > 100,000 EPS | In-memory parsing & canonical normalization | Empirical benchmark (`benchmarks/phase4_benchmark.py`) |
| **Engine Step Latency**| p50 < 100 µs | Normalization + quality check + route | Microbenchmark percentiles |
| **Failover Detection** | < 2.0 s | Primary silence to standby promotion | Heartbeat timeout fault injection |
| **Failover RTO** | < 1.0 s | Promotion execution to client acceptance | Failover transition benchmark |
| **Failover RPO** | 0 events | Committed WAL events lost | Catch-up sync requirement before promotion |
| **Accounting Precision**| 100% | Idempotent duplicate rejection | SQLite unique constraint on `idempotency_key` |

---

## 3. Independent Readiness Dimensions

MDRAP evaluates release readiness across 10 independent dimensions:
1. **Functional Readiness**: All documented features operate as specified.
2. **Correctness & Data Integrity**: Sequencing, dedup, and CRC32 WAL recovery verified.
3. **Performance & Capacity**: Throughput and latency verified under soak and saturation.
4. **Security Assurance**: Input sanitization, token hashing, and memory safety verified.
5. **Operational Readiness**: Observability, health endpoints, and alert rules active.
6. **Disaster Recovery**: Process restart, state restoration, and failover runbooks tested.
7. **Release Engineering**: Reproducible builds, manifests, and checksums verified.
8. **Compatibility Readiness**: Minor version backward compatibility guaranteed.
9. **Compliance Readiness**: Entitlement enforcement and usage reports generated.
10. **Customer Deployment Readiness**: Verified for Profile A and Profile B deployments.
