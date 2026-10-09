# Phase 4 Residual Risk Register

**Review Date**: 2026-10-09  
**Platform Version**: MDRAP v3.0  
**Status**: APPROVED WITH EXPLICIT OPERATIONAL LIMITATIONS  

---

## 1. Residual Risk Matrix

| Risk ID | Category | Risk Description | Severity | Likelihood | Compensating Control / Mitigation |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **RSK-001** | Kernel Bypass | AF_XDP / DPDK hardware bypass not supported on Windows/macOS or virtualized clouds | Medium | High | Deferred to bare-metal Linux with SR-IOV NICs. Platform falls back to optimized POSIX/Windows sockets with zero loss. |
| **RSK-002** | Rust SDK | Cargo toolchain not installed in default test environment | Low | Medium | Source code is fully aligned with SBE v1 packed struct layout; build deferred to Linux CI container. |
| **RSK-003** | Split-Brain Resolution | Network partition where heartbeats are blocked in both directions | Medium | Low | Epoch fencing token prevents stale primaries from writing. Ties broken deterministically by node ID. |
| **RSK-004** | Disk Capacity on WAL | IngestLog disk filling up during multi-day market bursts (>100M events) | High | Low | IngestLog segment rotation allows streaming offload of sealed `.log` segments to archival storage. Monitoring alert at 80% capacity. |
| **RSK-005** | Legacy YAML Config | Deprecated `config.yaml` usage in legacy scripts | Low | Low | Runtime loads YAML with explicit deprecation warning; documentation mandates `mdrap.toml`. |

---

## 2. Operational Limitations

1. **Hardware SR-IOV Dependency**: AF_XDP zero-copy kernel bypass requires Linux kernel 5.4+ with native driver support (e.g. Intel `ice`/`ixgbe` or Mellanox `mlx5`). Virtual machine environments run standard TCP/UDP socket adapters.
2. **Synchronous Metering Commit Ceiling**: SQLite fsync on every single individual event achieves ~300 commits/sec on mechanical or virtual storage. For throughput >10k eps, batching or grouped commit (`grouped_by_time` / `grouped_by_size`) must be enabled.
