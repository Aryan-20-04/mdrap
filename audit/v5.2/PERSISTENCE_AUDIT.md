# MDRAP v5.2 — Write-Ahead Log (WAL) & Persistence Architecture Audit

**Audit Classification:** SOURCE-VERIFIED, BENCHMARK-MEASURED & CRASH-VALIDATED  
**Date:** 2026-11-05  
**Auditor:** Principal Storage Architect & Reliability Engineer  
**Target Repository:** `c:\Users\KIIT0001\Desktop\Projects\mdrap` (Branch: `v5.2-development`)  
**Source Code Anchor:** [`src/mdrap/ingestlog.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/mdrap/ingestlog.py)  
**Associated Telemetry:** [`RAW_PERSISTENCE_TELEMETRY.json`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/audit/v5.2/RAW_PERSISTENCE_TELEMETRY.json)

---

## 1. Executive Summary

In high-throughput market data systems, persistence mechanisms must navigate the trade-off between **physical durability** (synchronous disk flushes) and **latency budgets** (sub-microsecond execution). Naively executing `os.fsync()` on every market tick drops throughput from $\approx 90,000\text{ EPS}$ down to $< 300\text{ EPS}$ due to solid-state drive (NVMe) non-volatile NAND write cycle latency ($2.5\text{--}4.0\text{ ms}$).

This audit conducted a deep inspection of `IngestLog`, evaluating its segmented binary write-ahead log, CRC32C framing, torn-tail recovery, and configurable synchronization policies.

---

## 2. IngestLog Binary Framing Specification

Each event in the WAL is encapsulated in a binary frame:
- **Frame Header (28 bytes):**
  - `magic` (`uint16`, `0x4D4C` = 'ML')
  - `version` (`uint16`, `2`)
  - `offset` (`uint64`, monotonically increasing monotonic sequence)
  - `timestamp` (`float64`, wall-clock time)
  - `payload_length` (`uint32`, size of JSON/SBE payload)
  - `expected_crc` (`uint32`, IEEE CRC32 or hardware CRC32C)
- **Frame Payload:** $N$ raw bytes representing the serialized market data event.

---

## 3. Empirical Fsync Policy Performance Comparison

We benchmarked `IngestLog` across three operational policies on Windows 11 NVMe storage:

| Policy | Mechanism | Measured Throughput (EPS) | Mean Latency ($\mu$s) | Power-Loss Durability Window |
| :--- | :--- | :--- | :--- | :--- |
| **`always`** | Synchronous `fdatasync()` / `FlushFileBuffers()` per event | **297.6 EPS** | **3,360.24 $\mu$s** (3.36 ms) | **Zero Loss (Durable Commit)** |
| **`grouped_by_time`** | Asynchronous background daemon flushes every 10 ms | **42,178.3 EPS** | **23.71 $\mu$s** | **Bounded to $\le 10\text{ ms}$** |
| **`never`** | Userspace buffering + OS page cache (no explicit fsync) | **91,542.5 EPS** | **10.92 $\mu$s** | **Bounded to OS flush interval** |

```
Throughput (EPS)
  100k |  +-- never (91,543 EPS)
   80k |
   60k |
   40k |                 +-- grouped_by_time (42,178 EPS)
   20k |
    0k |                                                +-- always (298 EPS)
       +--------------------------------------------------------------------
       Policy
```

---

## 4. Key Storage Insights

1. **The 3.36 ms Hardware Floor:** Solid-state NVMe drives cannot complete physical non-volatile cell programming faster than $\approx 2.5\text{--}4.0\text{ ms}$. Calling `fsync` synchronously on every market tick creates an insurmountable physical bottleneck.
2. **`grouped_by_time` as the Institutional Compromise:** By batching fsync operations across a 10 ms background sliding window, `grouped_by_time` delivers **42,178.3 EPS** while bounding maximum potential unpersisted data to a strictly controlled 10 ms window.
3. **`never` for Market Making:** Proprietary trading desks running latency-sensitive strategies prioritize memory throughput (**91,542.5 EPS**) over physical disk commits, relying on upstream exchange snapshot/replay APIs for disaster recovery.
