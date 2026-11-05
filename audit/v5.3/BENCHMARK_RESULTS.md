# MDRAP v5.3 — Comprehensive Benchmark Results & Telemetry Summary

## Hardware & Environment Baseline
- **OS**: Windows 11 Home (Build 26100, 64-bit AMD64)
- **CPU**: AMD Ryzen 7 5800H with Radeon Graphics (8 Cores, 16 Threads @ 3.20 GHz)
- **RAM**: 16.0 GB DDR4
- **Python**: Python 3.13.1 (tags/v3.13.1:0671451)
- **C Compiler**: MSVC 19.42 / Clang ABI Compatible

---

## 1. Boundary Benchmark Scorecard (v5.3 Measured)

Source: `audit/v5.3/raw/benchmarks_raw.json`

| Pipeline Boundary | Subsystem Measured | Throughput (EPS) | Mean Latency ($\mu$s) | p50 ($\mu$s) | p99 ($\mu$s) | Max ($\mu$s) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Boundary A** | Raw Feed Generator & Ingress | 36,764,705.9 | 0.03 | 0.02 | 0.05 | 0.80 |
| **Boundary B** | Gateway Schema Normalization | 176,470.8 | 5.67 | 5.10 | 12.40 | 145.20 |
| **Boundary C1**| **Native C Fastpath** | **510,820.1** | **1.84** | **1.60** | **4.10** | **158.20** |
| **Boundary C2**| Pure Python Fallback Engine | 170,079.5 | 5.72 | 4.50 | 18.00 | 794.50 |
| **Boundary D1**| Avellaneda–Stoikov Math | 332,702.1 | 3.01 | 2.80 | 6.50 | 45.10 |
| **Boundary D2**| Linear Inventory Skew Math | 479,249.0 | 2.09 | 1.90 | 4.80 | 32.40 |
| **Boundary E** | Engine State Transitions & Undo | 38,451.9 | 26.01 | 24.20 | 58.00 | 312.00 |
| **Boundary G1**| Persistence (`never`) | 40,152.5 | 24.90 | 23.50 | 54.00 | 290.00 |
| **Boundary G2**| Persistence (`grouped_by_time`) | 48,459.8 | 20.63 | 19.20 | 45.00 | 210.00 |
| **Boundary G3**| Persistence (`always`) | 36,527.4 | 27.38 | 25.10 | 65.00 | 420.00 |
| **Boundary H** | **Full Stateful End-to-End** | **21,850.1** | **45.20** | **41.00** | **119.50** | **987.50** |

---

## 2. Multicore Scaling Summary

Source: `audit/v5.3/raw/multicore_scaling.json`

- **Stateless Ingress Scaling**:
  - 1 Worker: 58,846.3 EPS
  - 2 Workers: 82,510.9 EPS (1.40x)
  - 4 Workers: 102,080.2 EPS (1.73x)
  - 8 Workers: 105,814.9 EPS (1.80x)
- **Full Stateful Pipeline Scaling**:
  - 1 Worker: 23,672.6 EPS
  - 2 Workers: 31,408.8 EPS (1.33x)
  - 4 Workers: 54,676.8 EPS (2.31x)
  - 8 Workers: 82,055.5 EPS (3.47x)
