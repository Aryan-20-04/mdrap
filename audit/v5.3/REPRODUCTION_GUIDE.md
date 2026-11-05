# MDRAP v5.3 — Independent Reproduction & Verification Guide

## Purpose
This guide allows any independent systems engineer, quantitative researcher, or release auditor to reproduce all v5.3 benchmark numbers, test suites, and audit findings directly from the repository.

---

## 1. Environment Setup

### 1.1 Prerequisites
- Python 3.11+ (Python 3.13.1 recommended)
- C Compiler: MSVC 2022 (Windows) or GCC 11+ / Clang 14+ (Linux)
- Git 2.40+

### 1.2 Checkout Target
```powershell
git checkout v5.3-engineering-audit
```

---

## 2. Reproduction Commands

### 2.1 Run Unit & Quantitative Remediation Test Suite
To verify the remediated `force_pure` fastpath fallback, linear skew MM decoupling, FX basis valuation, and matching engine risk guards:
```powershell
pytest tests/test_v53_quantitative_remediation.py -v
```
Expected output: `6 passed in ~1.2s`.

### 2.2 Run Full Unified Verification & Benchmark Runner
To reproduce all pipeline boundary measurements, C vs. Python speedups, multicore scaling, quantitative strategy tear sheets, and fault injection:
```powershell
python benchmarks/run_v53_unified_benchmark.py
```
Outputs generated in:
- `audit/v5.3/raw/benchmarks_raw.json`
- `audit/v5.3/raw/multicore_scaling.json`
- `audit/v5.3/raw/quantitative_audit.json`
- `audit/v5.3/raw/fault_injection_results.json`

### 2.3 Verify True Native C vs Pure Python Fallback
Run a standalone parity and throughput check:
```powershell
# Native C Fastpath
python -c "from mdrap.fastpath import FastQualityEngine; fqe = FastQualityEngine(); print('Native C Active:', fqe.is_native)"

# Pure Python Fallback
python -c "from mdrap.fastpath import FastQualityEngine; fqe = FastQualityEngine(force_pure=True); print('Native C Active:', fqe.is_native, 'Fallback:', type(fqe._fallback_engine))"
```

### 2.4 Verify Cross-Venue Arbitrage Denial
```powershell
python -c "import sys; sys.path.insert(0, 'packages/mdrap-strategies'); from mdrap_strategies.fx_basis import DenominationAwareValuator; val = DenominationAwareValuator(); res = val.analyze_cross_venue_spread('Coinbase', 'BTC-USD', 64200.0, 4.0, 'Binance', 'BTC-USDT', 64276.09, 7.0, 1.0); print(res.to_dict())"
```
Expected: `is_profitable: False`, `rejection_reason: 'FEE_DRAG_UNPROFITABLE'`.
