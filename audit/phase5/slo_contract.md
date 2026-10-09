# Phase 5 Service Level Objective (SLO) Contract

**Platform**: MDRAP v3.0.0  
**Target Profile**: Profile A (Single-Node Pilot)  
**Date**: 2026-10-09  

---

## 1. Quantitative Production SLOs

| Dimension | Service Level Indicator (SLI) | Target SLO | Measurement Method |
| :--- | :--- | :--- | :--- |
| **Availability** | Process uptime & HTTP `/health` 200 response | **>= 99.9%** | Synthetic monitor every 10s |
| **Ingress Loss** | Unaccounted drops on valid schema frames | **Strictly 0.0%** | Ingress count vs Egress count |
| **Quality Accuracy**| Strict adherence to priority (`INVALID > SUSP > VALID`) | **100.0%** | Quality engine rule audit |
| **Hot Path Latency**| Ingress poll to SBE broadcast (Median p50) | **<= 100 µs** | High-precision perf_counter_ns |
| **Tail Latency** | Ingress poll to SBE broadcast (Tail p99) | **<= 500 µs** | 50k soak percentile tracking |
| **Durability RTO** | Time to recover WAL on cold restart | **<= 5.0 s** per 100k ev | Cold replay benchmark |
| **Durability RPO** | Maximum uncommitted event loss on process crash | **<= 0 events** (always fsync) | IngestLog segment boundary scan |
