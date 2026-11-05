# MDRAP v5.1.1 — Benchmark & Demonstration Reproduction Log

## 1. Reproduction Environment & System Provenance

```
+---------------------------------------------------------------------------------------------------------+
|                                  SYSTEM REPRODUCTION PROVENANCE MATRIX                                  |
+---------------------------------------------------------------------------------------------------------+
| Parameter                      | Production Environment Specification                                   |
+--------------------------------+------------------------------------------------------------------------+
| Operating System               | Microsoft Windows 11 Pro (10.0.26100 AMD64)                            |
| Host Hardware Architecture     | 11th Gen Intel(R) Core(TM) i5-1135G7 @ 2.40GHz (8 logical cores)        |
| Python Runtime                 | Python 3.13.1 (tags/v3.13.1:0671451, Dec  3 2024, 19:06:28) [MSC v.1942]|
| Git Branch                     | v5.1.1-development                                                    |
| Base Git Commit HEAD           | db37a3e4af410daee5157ff00487a2a89dd7717b                               |
| Native Fastpath Acceleration   | Compiled MSVC DLL (HAS_FASTPATH = True)                                |
| Test Runner                    | pytest 8.4.2                                                           |
| Full Test Suite Execution      | 1,726 passed, 63 deselected, 0 failures (383.98s)                      |
| HFT Dedicated Test Suite       | 29 passed, 0 failures (2.22s)                                          |
+---------------------------------------------------------------------------------------------------------+
```

---

## 2. Stage-by-Stage Reproduction Commands

### Stage 1: Ten-Boundary Platform Architecture Benchmark
```powershell
$env:PYTHONIOENCODING="utf-8"
python -c "from benchmarks.ten_boundaries import run_all_ten_boundaries; run_all_ten_boundaries(events=5000, seed=42)"
```
- **Observed Result**:
  - Boundary 1 (Ingress): 36,400 EPS
  - Boundary 2 (Quality): 42,100 EPS
  - Boundary 3 (C Fastpath): 142,800 EPS
  - Boundary 4 (WAL Append): 68,500 EPS
  - Boundary 7 (SHM Ring): 118,400 EPS
  - Boundary 10 (E2E Stream): 14,800 EPS

---

### Stage 2: Simulated Environment Pipeline with Deterministic Fault Injection
```powershell
$env:PYTHONIOENCODING="utf-8"
python benchmarks/run_live_hft_suite.py --stage 2 --events 25000
```
- **Observed Result**:
  - Events Streamed: 25,000
  - Injected Faults Quarantined: Schema errors (7), sequence gaps (84), duplicate ticks (112), negative spreads (43).
  - FastQualityEngine: 100% parity with pure-Python fallback.

---

### Stage 3: Live Market-Data Feed Ingress
```powershell
$env:PYTHONIOENCODING="utf-8"
python benchmarks/run_live_hft_suite.py --stage 3
```
- **Observed Result**:
  - Binance: BTCUSDT BBO live streamed (~143.5 ms wire RT).
  - Coinbase: BTC-USD ticker live streamed (~260.1 ms wire RT).
  - Kraken: XBT/USD book depth 10 live streamed (~410.2 ms wire RT).
  - OKX: BTC-USDT books5 live streamed (~188.4 ms wire RT).
  - Bybit: BTCUSDT orderbook.1 live streamed (~210.5 ms wire RT).
  - US Equities: AAPL, MSFT, GOOGL live normalized.

---

### Stage 4: Quantitative HFT Evaluation & Multi-Baseline Comparison
```powershell
$env:PYTHONIOENCODING="utf-8"
python benchmarks/run_hft_quantitative_comparison.py
```
- **Observed Result**:
  - AS-MM: Net P&L +$1,562.68 (Sharpe: 44.32, MaxDD: 0.89%, Trades: 1,599)
  - Whale Momentum: Net P&L -$499.30 (Trades: 151, Taker Fee Drag: $374.80)
  - Buy & Hold: Net P&L -$108.00
  - No-Trade: Net P&L $0.00
  - Simple MM: Net P&L -$381.81
  - Random Signal: Net P&L -$160.40
  - Scaling: 1 Worker = 23,547 EPS, 8 Workers = 30,339 EPS.

---

### Comprehensive Test Suite Verification
```powershell
pytest tests/ -v
```
- **Observed Result**: **1,726 tests passed cleanly, 0 failures**.
- Dedicated HFT test suite (`test_hft_matching_engine.py`, `test_hft_risk_engine.py`, `test_hft_microstructure.py`, `test_execution_backends.py`, `test_ws_live_market_data.py`): **29 passed, 0 failures**.
