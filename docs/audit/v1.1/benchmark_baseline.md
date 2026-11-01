# MDRAP v4.0.0 — Phase 0 Benchmark Baseline & Policy

## 1. Workload Equivalence Tiers (Playbook §6)

| Tier | Name | Target Workload | Scope & Invariants |
|:---:|:---|:---|:---|
| **B1** | Native Rule Microbenchmark | Single-message SBE/C rule decode | Pure in-memory CPU instruction path (`fastpath.c`) |
| **B2** | Parity Benchmark | Identical test vectors across C and Python | Proves zero semantic divergence between engines |
| **B3** | Full In-Memory Engine | Step processing without disk I/O | Sequence, dedup, price corridor, BBO |
| **B4** | WAL Direct Append | IngestLog append without fsync (`none`) | Raw sequential framing throughput |
| **B5** | Durable End-to-End | Submit + WAL `always` / `grouped_by_time` | Real disk I/O durability boundary |
| **B6** | Multi-Feed Pipeline | Multiple feeds, reconciliation, projection | Realistic end-to-end institutional sidecar |
| **B7** | Replay & Recovery | Full log scan & state fold | Replay eps & snapshot checkpoint speed |

## 2. Hardware Environment Manifest
- **Platform**: Windows 11 Enterprise x86_64
- **CPU**: AMD64 Family 25 Model 80 Stepping 0 (8 physical / 16 logical cores)
- **Memory**: 16 GB RAM
- **Storage**: NVMe PCIe SSD
- **Compiler**: Microsoft Visual C++ 19.42 (MSVC)
- **Python**: 3.13.1 64-bit

## 3. Baseline Measurements
- Test suite runtime: 1,544 unit tests in 270.03s (~5.7 tests/sec comprehensive coverage).
- E1-E15 reproduction suite: 15 comprehensive defect reproductions in 1.40s.
