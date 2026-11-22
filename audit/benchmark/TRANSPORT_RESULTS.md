# MDRAP v6.0.8 — Transport Benchmark Results (TCP vs SHM)

**Audit Timestamp**: 2026-11-21  
**Target Environment**: Windows 11 AMD64 (12 logical cores, Intel Core @ 1.70 GHz, 15.72 GB RAM), Python 3.13.1  
**Git Commit**: `51f99e8e18768860540b534297b02b507cefce05` (`main`)  
**Data Sources**: `audit/benchmark/raw/transport_benchmark.json`, `audit/benchmark/raw/transport_memory_microbench.json`

---

## 1. Executive Summary: Inter-Process Communication & Streaming

MDRAP provides two distinct streaming distribution mechanisms:
1. **Shared Memory (SHM v3)**: A lock-free seqlock ring buffer (`src/mdrap/shm.py`) backed by native Windows / POSIX shared memory with 64-byte aligned header cache lines and two-phase atomic sequence commit.
2. **Streaming TCP (`StreamClient`)**: High-speed JSON socket streaming (`src/mdrap/service.py`) supporting non-blocking subscriber queues, mTLS authentication, and client isolation.

### Key Performance Findings
- **Throughput Supremacy of SHM**: Under multi-consumer fanout, SHM achieves **271,413.7 aggregate EPS** across 4 consumers, compared to **17,096.7 EPS** for TCP—an **almost 16x throughput multiplier**.
- **CPU Efficiency**: Delivering 50,000 events to 4 consumers via SHM consumed **0.70 seconds of CPU time**, while TCP consumed **17.48 seconds**—making SHM **25x more CPU efficient**.
- **Uncontended IPC Transit Latency**: Dedicated micro-benchmarking demonstrates that uncontended publish-to-read transit latency in SHM is **4.8 µs (p50)** and **9.9 µs (p99)**.
- **TCP Transit Latency**: Direct TCP point-to-point streaming delivers **46.5 µs (p50)** and **168.0 µs (p99)** at 20,678 EPS for a single consumer.

---

## 2. Multi-Consumer Scaling Matrix (50,000 Events per Test)

Both transports were benchmarked under identical 50,000-event workloads with 1, 2, and 4 concurrent consumers:

| Transport | Consumers | Aggregate Deliveries | Delivery Rate | Producer EPS | Aggregate Throughput | Transit / Dwell Latency (p50) | Latency p95 | Latency p99 | Latency p99.9 | Latency Max | CPU Time | Net RSS |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **SHM** | 1 | 50,000 | 100.00% | 207,431.8 | **207,431.8 EPS** | 56,525.5 µs* | 81,658.7 µs | 89,162.3 µs | 89,394.8 µs | 89,504.3 µs | **0.22 s** | +2.72 MB |
| **SHM** | 2 | 100,000 | 100.00% | 122,632.4 | **245,264.9 EPS** | 128,480.1 µs* | 176,551.0 µs | 193,948.8 µs | 194,392.8 µs | 194,484.2 µs | **0.38 s** | +3.40 MB |
| **SHM** | 4 | 200,000 | 100.00% | 67,853.4 | **271,413.7 EPS** | 219,934.4 µs* | 321,052.8 µs | 336,893.5 µs | 346,124.6 µs | 346,301.0 µs | **0.70 s** | +5.64 MB |
| **TCP** | 1 | 50,000 | 100.00% | 20,678.1 | **20,678.1 EPS** | **46.5 µs** | 120.4 µs | 168.0 µs | 270.7 µs | 672.8 µs | 3.08 s | +0.42 MB |
| **TCP** | 2 | 100,000 | 100.00% | 8,414.9 | **16,829.7 EPS** | **81.3 µs** | 224.0 µs | 568.4 µs | 1,382.6 µs | 2,244.3 µs | 9.17 s | +1.23 MB |
| **TCP** | 4 | 200,000 | 100.00% | 4,274.2 | **17,096.7 EPS** | **128.3 µs** | 363.3 µs | 543.3 µs | 855.2 µs | 4,403.1 µs | 17.48 s | +3.79 MB |

*\*Note on SHM Scaling Latency: In the unthrottled burst benchmark, the producer fills all 50,000 slots in ~0.24 seconds into the 65,536-slot ring buffer. Because the producer runs at >200,000 EPS while the consumer reader loop unpacks slots sequentially in Python, the tail slots experience ring buffer queue dwell time (56–220 ms) before extraction. For uncontended single-message transit latency, see §3 below.*

---

## 3. Micro-Benchmark: Wire Protocol & IPC Transit Latency

To eliminate queue dwell artifacts and measure true wire protocol and IPC overhead, dedicated micro-benchmarks were executed:

### Simple Binary Encoding (SBE) vs. JSON Text Loads (100,000 Messages)
- **SBE Binary Unpack**: **372,696.4 EPS** (2,683.1 ns per message)
- **JSON Text Loads**: **140,183.0 EPS** (7,133.5 ns per message)
- **SBE Acceleration**: **2.66x wire decoding speedup** over JSON text

### NASDAQ ITCH 5.0 Wire Protocol Parser (100,000 Messages)
- **Throughput**: **398,982.9 messages/sec**
- **Average Parse Latency**: **2,506.4 ns (2.5 µs)**

### Seqlock Shared Memory (SHM v3) Uncontended Roundtrip (50,000 Operations)
- **Throughput**: **182,009.9 roundtrips/sec**
- **p50 Latency**: **4.8 µs (4,800 ns)**
- **p90 Latency**: **6.2 µs (6,200 ns)**
- **p95 Latency**: **7.0 µs (7,000 ns)**
- **p99 Latency**: **9.9 µs (9,900 ns)**
- **p99.9 Latency**: **51.5 µs (51,500 ns)**

---

## 4. Architectural Analysis: When to Use SHM vs. TCP

| Operational Dimension | Shared Memory (SHM v3) | Streaming TCP (`StreamClient`) |
|:---|:---|:---|
| **Best Used For** | Co-located ultra-low-latency sidecars, high-frequency internal readers, microsecond audit observers | Cross-machine distribution, container/network boundary streaming, legacy subscribers |
| **Max Sustained EPS** | > 270,000 EPS | ~ 20,000 EPS |
| **Transit Latency (p50)**| **4.8 µs** | 46.5 µs |
| **Tail Latency (p99)** | **9.9 µs** | 168.0 µs |
| **Zero-Copy Status** | Seqlock ring buffer. C-level is true zero-copy; Python layer unpacks into lightweight dict | Socket I/O + JSON serialization overhead |
| **Client Isolation** | Read-only memory mapping; slow readers cannot stall the publisher | Dedicated 1,000-frame queue with proactive eviction of stalled clients |
| **Security Boundary** | Strict OS-level permissions (`0600`), epoch verification, crash detection | Optional institutional mTLS / TLS 1.3 with client certificate verification |
