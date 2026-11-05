# MDRAP v5.2 — Independent Benchmark & Audit Reproduction Guide

**Audit Classification:** REPRODUCIBILITY & OPERATIONAL GUIDE  
**Date:** 2026-11-05  
**Auditor:** Principal Infrastructure Auditor & DevOps Specialist  
**Target Repository:** `c:\Users\KIIT0001\Desktop\Projects\mdrap` (Branch: `v5.2-development`)  
**Platform Requirement:** Windows 10/11 x86-64 or Linux AMD64, Python 3.11+, C Compiler (MSVC 19.40+ or GCC 11+)  

---

## 1. Overview & Determinism Guarantee

Every benchmark figure, latency measurement, quantitative backtest, and parity verdict presented in the MDRAP v5.2 audit report is **100% reproducible**. All synthetic random processes utilize fixed deterministic seeds (`seed=42`).

This guide provides the exact commands, execution sequence, and expected telemetry outputs.

---

## 2. Environment Verification & Compilation

### Step 1: Ensure Clean Working Tree on Branch `v5.2-development`
```powershell
git status
git branch --show-current
# Expected output: On branch v5.2-development
```

### Step 2: Verify Python Environment & Dependencies
```powershell
python --version
# Expected: Python 3.11.x, 3.12.x, or 3.13.x
pip install rich pytest
```

---

## 3. Authoritative Audit Execution Pipeline

Execute the following 7 test suites in sequence from the repository root:

### Stage 1: Core Architectural Reproduction Suite (Phase 1)
Measures the 5 isolated system boundaries (Native C, Python, SHM v3, IngestLog WAL, and Full E2E Pipeline).
```powershell
python benchmarks/run_v52_reproduction_suite.py
```
- **Execution Time:** ~25 seconds
- **Output Artifacts:** [`RAW_BENCHMARK_RESULTS.json`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/audit/v5.2/RAW_BENCHMARK_RESULTS.json)
- **Expected Key Figures:**
  - Native Fastpath: $> 550,000\text{ EPS}$ ($1.5\text{--}1.8\,\mu\text{s}$)
  - Pure Python Fallback: $> 550,000\text{ EPS}$ ($1.4\text{--}1.8\,\mu\text{s}$)
  - SHM v3 Ring Buffer: $> 250,000\text{ EPS}$ ($3.0\text{--}4.5\,\mu\text{s}$)
  - Buffered WAL: $> 85,000\text{ EPS}$ ($8.0\text{--}12.0\,\mu\text{s}$)
  - Direct fsync WAL: $250\text{--}350\text{ EPS}$ ($2.5\text{--}3.5\text{ ms}$)
  - Full E2E Pipeline: $> 25,000\text{ EPS}$ ($30\text{--}40\,\mu\text{s}$)

---

### Stage 2: Quantitative Research & Sharpe Ratio Audit (Phase 2)
Executes Sharpe ratio forensics, walk-forward validation (60/20/20), and 7-strategy comparisons.
```powershell
python benchmarks/run_v52_quantitative_audit.py
```
- **Execution Time:** ~15 seconds
- **Output Artifacts:** [`RAW_STRATEGY_TELEMETRY.json`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/audit/v5.2/RAW_STRATEGY_TELEMETRY.json)
- **Expected Key Figures:**
  - Honest Daily Annualized Sharpe: **0.37** ($t\text{-stat} = 0.412, p = 0.6806$)
  - Invalidate Fallacy Sharpe: **~57.14** (Disproven)
  - AS-MM Net PnL (Baseline): **+\$1,562.68**
  - AS-MM Net PnL (10ms Latency): **+\$26.04** (98.3% PnL collapse)
  - Fixed-Spread MM Net PnL: **-\$381.81** (Loss)

---

### Stage 3: Execution Simulator & Microstructure Audit (Phase 3)
Measures queue position fill latency, 2ms cancellation races, and multi-horizon adverse selection markouts.
```powershell
python benchmarks/run_v52_phase3_matching_audit.py
```
- **Execution Time:** ~2 seconds
- **Output Artifacts:** [`RAW_MATCHING_TELEMETRY.json`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/audit/v5.2/RAW_MATCHING_TELEMETRY.json)
- **Expected Key Figures:**
  - Queue Fill Latency: 138 ms (top of queue) $\to$ 199 ms (1,500 units depth)
  - Cancel Success at 1.0 ms: **100.0%**
  - Cancel Success at 5.0 ms: **0.0% (100% fill leakage)**
  - 100ms Adverse Selection Markout: **49.67% of trades adverse**

---

### Stage 4: Concurrency & Multicore Scaling Audit (Phase 4)
Runs concurrent seqlock reader/writer contention and multicore process pool benchmarks.
```powershell
python benchmarks/run_v52_phase4_concurrency_audit.py
```
- **Execution Time:** ~10 seconds
- **Output Artifacts:** [`RAW_CONCURRENCY_TELEMETRY.json`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/audit/v5.2/RAW_CONCURRENCY_TELEMETRY.json)
- **Expected Key Figures:**
  - Seqlock Contention: **213,943.4 EPS** across 4 concurrent readers
  - Torn Reads / Data Corruptions: **0 (100% Data Integrity)**
  - 1 Worker: ~112,000 EPS | 2 Workers: ~121,000 EPS

---

### Stage 5: Native Hot-Path, SBE & Parity Audit (Phase 5)
Verifies 100% bit-exact parity between C and Python, benchmarks SBE vs JSON, and parses NASDAQ ITCH 5.0.
```powershell
python benchmarks/run_v52_phase5_native_audit.py
```
- **Execution Time:** ~4 seconds
- **Output Artifacts:** [`RAW_NATIVE_TELEMETRY.json`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/audit/v5.2/RAW_NATIVE_TELEMETRY.json)
- **Expected Key Figures:**
  - Differential Parity: **100.0000% Bit-Exact (0 mismatches across 10,000 events)**
  - SBE Wire Speedup: **2.95x faster than JSON** (167k vs 56k EPS)
  - SBE Bandwidth Reduction: **60.86%** (128 bytes vs 327 bytes)
  - NASDAQ ITCH 5.0 Throughput: **133,707.4 EPS** (7.48 $\mu$s latency)

---

### Stage 6: Persistence, WAL & Crash Recovery Audit (Phase 6)
Benchmarks WAL fsync policies, tests crash recovery, and injects byte-level WAL corruption.
```powershell
python benchmarks/run_v52_phase6_persistence_audit.py
```
- **Execution Time:** ~6 seconds
- **Output Artifacts:** [`RAW_PERSISTENCE_TELEMETRY.json`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/audit/v5.2/RAW_PERSISTENCE_TELEMETRY.json)
- **Expected Key Figures:**
  - Fsync 'never': **~91,500 EPS** (10.9 $\mu$s)
  - Fsync 'always': **~298 EPS** (3.36 ms)
  - Crash Recovery: **100% Exact State Reconstruction**
  - Torn Tail Truncation: **True** (Auto-truncated partial tail at EOF)
  - Mid-File Corruption: **Caught** (`IngestLogCorruptError` raised cleanly)

---

### Stage 7: Feed Integrity & Cross-Venue Basis Audit (Phase 7)
Deconstructs the BTC-USD vs BTC-USDT spread and executes feed fault injection drills.
```powershell
python benchmarks/run_v52_phase7_feed_integrity_audit.py
```
- **Execution Time:** ~2 seconds
- **Output Artifacts:** [`RAW_FEED_INTEGRITY_TELEMETRY.json`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/audit/v5.2/RAW_FEED_INTEGRITY_TELEMETRY.json)
- **Expected Key Figures:**
  - Apparent Spread: +$73.50 (+11.45 bps)
  - Taker Fees + Basis: -$134.95
  - Net Realized PnL: **-$61.45 per Bitcoin (-9.57 bps Loss)**
  - Truly Executable: **False**

---

## 4. Full Regression Verification Suite

To run the complete platform regression suite:
```powershell
pytest tests/ -v
```
All core tests must pass cleanly.
