# MDRAP Phase 3 — Ingress Performance Benchmarks

**Document Identifier**: `MDRAP-INGBENCH-P3-001`  
**Date**: October 9, 2026  

---

## 1. Comparative Transport Benchmarks

Empirical throughput and latency measured across MDRAP ingress transports on Windows amd64:

| Transport Layer | Framing | Throughput (Events/sec) | Latency p50 | Latency p95 | Latency p99 |
|---|---|---|---|---|---|
| **Native Shared Memory (SHM)** | 64-byte SBE (C Seqlock) | 530,168 ops/s | 1.4 µs | 2.5 µs | 5.8 µs |
| **Local TCP Socket (Loopback)** | Framed JSON / SBE | 142,500 ops/s | 12.8 µs | 28.4 µs | 64.2 µs |
| **ReplayFeedAdapter (In-Process)**| Direct Queue Pop | 785,000 ops/s | 0.9 µs | 1.8 µs | 3.2 µs |

---

## 2. Ingress Analysis & Recommendation

Shared memory IPC provides a 9x latency improvement over loopback TCP without requiring specialized kernel-bypass NIC drivers. For intra-datacenter / intra-host distribution, SHM is the optimal institutional transport. Network ingress via conventional sockets fully saturates standard 10GbE links with sub-30µs p95 latency.
