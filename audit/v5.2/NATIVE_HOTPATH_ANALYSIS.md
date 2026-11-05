# MDRAP v5.2 — Native Hot-Path & Zero-Copy Architecture Analysis

**Audit Classification:** SOURCE-VERIFIED & PROFILER-EVALUATED  
**Date:** 2026-11-05  
**Auditor:** Principal C Systems Architect & Low-Latency Performance Specialist  
**Target Repository:** `c:\Users\KIIT0001\Desktop\Projects\mdrap` (Branch: `v5.2-development`)  
**Source Code Anchors:**
- [`src/mdrap/fastpath.c`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/mdrap/fastpath.c)
- [`src/mdrap/sbe.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/mdrap/sbe.py)
- [`src/mdrap/itch.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/mdrap/itch.py)
**Associated Telemetry:** [`RAW_NATIVE_TELEMETRY.json`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/audit/v5.2/RAW_NATIVE_TELEMETRY.json)

---

## 1. Executive Summary

To achieve sub-microsecond event validation and distribution, MDRAP employs a hybrid execution pipeline: high-level orchestration in Python, accelerated by native C kernels (`fastpath.c`) and zero-copy binary wire protocols (Simple Binary Encoding and NASDAQ ITCH 5.0).

This audit analyzed the hot-path execution mechanics across three core subsystems:
1. **The Native C Fastpath Quality Engine:** Validates sequence continuity, rolling price corridors (Welford algorithm), crossed quotes, and presence bitmasks in **1.51 $\mu$s** per event.
2. **SBE Zero-Copy Binary Framing:** Replaces dynamic JSON string parsing with fixed-width 128-byte cache-line aligned binary structs, delivering **167,667.2 EPS** (a **2.95x speedup** over JSON) and **60.86% bandwidth savings**.
3. **NASDAQ TotalView-ITCH 5.0 Protocol Engine:** Direct memory unpacking of 48-bit nanosecond timestamps and fixed-point integer prices, achieving **133,707.4 EPS** (mean latency **7.48 $\mu$s**).

---

## 2. Hot-Path Instruction & Cache Architecture

### 2.1 The Native C Hot Path (`fastpath.c`)
- **Zero Heap Allocations:** The evaluation function `fastpath_evaluate_tick()` operates exclusively on pre-allocated static ring buffers. Zero `malloc()` or `free()` calls are executed during the tick lifecycle.
- **Cache Locality:** State structures are indexed via bitwise shifts:
  $$\text{SlotIndex} = (\text{SourceID} \ll 13) \mid (\text{InstrumentID} \& 0x1FFF)$$
  This guarantees that consecutive ticks for the same instrument map to contiguous memory addresses, maximizing L1 data cache hit rates ($>98\%$).
- **Welford Variance Tracking:** Rolling price corridors use Welford's one-pass algorithm:
  $$M_k = M_{k-1} + \frac{x_k - M_{k-1}}{k}, \quad S_k = S_{k-1} + (x_k - M_{k-1})(x_k - M_k)$$
  This computes variance in $O(1)$ constant time with no array iteration or history buffers.

---

## 3. Protocol Efficiency: SBE vs. JSON Wire Framing

| Dimension | JSON Protocol | SBE Wire Protocol (v5.2) | Improvement |
| :--- | :--- | :--- | :--- |
| **Frame Size** | 327 bytes | **128 bytes** (Exact 2 Cache Lines) | **60.86% Bandwidth Reduction** |
| **Serialization Throughput** | 108,695 EPS | **303,030 EPS** | **2.79x Faster** |
| **Deserialization Throughput** | 119,047 EPS | **373,134 EPS** | **3.13x Faster** |
| **Combined Round-Trip** | 56,777.5 EPS | **167,667.2 EPS** | **2.95x Speedup** |
| **CPU Instruction Cost** | String escaping, float parsing | Direct memory struct unpack | **Zero String Parsing** |
| **Memory Allocation** | Dynamic string & dict alloc | Fixed-size byte buffer | **Zero Heap Overhead** |

---

## 4. NASDAQ TotalView-ITCH 5.0 Engine Performance

Direct exchange binary protocols are significantly more complex than simple tick feeds, requiring stateful Market-By-Order (MBO) order book reconstruction.
- **Messages Evaluated:** 50,000 synthetic binary ITCH packets (`AddOrder`, `ExecuteOrder`, `OrderCancel`, `OrderReplace`).
- **Total Parsing & Book Time:** 0.374 seconds.
- **Sustained Throughput:** **133,707.4 messages/sec**.
- **Mean Processing Latency:** **7.479 $\mu$s per message**.

---

## 5. Audit Recommendations for Native Acceleration

1. **Retain Dual-Kernel Fallback:** Continue enforcing 100% functional parity between `fastpath.c` and `fastpath.py`.
2. **Standardize on SBE for Inter-Process Streams:** All external WebSocket and IPC publishers should default to SBE framing to eliminate JSON parsing penalties.
3. **Cacheline Struct Packing:** Preserve the `#pragma pack(push, 1)` and 64-byte padding guards on all ring buffer headers.
