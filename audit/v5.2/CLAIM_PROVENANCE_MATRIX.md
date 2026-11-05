# MDRAP v5.2 — Claim Provenance & Verification Matrix

## 1. Provenance Classification Methodology
Every prior claim, benchmark figure, and architectural metric is classified into one of six rigorous evidentiary categories:
1. **Independently Reproduced**: Measured and verified under identical conditions with zero material discrepancies.
2. **Reproduced with Discrepancies**: Verified, but with identified variations in boundaries, configuration, or environment.
3. **Partially Verified**: Underlying mechanism exists and functions, but high-level claim is qualified.
4. **Not Reproduced**: Failed reproduction under identical workload or test parameters.
5. **Unsupported**: Claim has no underlying code, test fixture, or reproducible benchmark script.
6. **Incorrect**: Mathematically or empirically false (e.g. flawed formula, unexecutable trade, or invalid annualization).

---

## 2. Institutional Provenance Matrix

```
+--------------------------------------------------------------------------------------------------------------------+
|                                      MDRAP v5.1.1 CLAIM PROVENANCE MATRIX                                          |
+--------------------+-------------------------+-------------------------------+-------------------------------------+
| Claim # | Domain   | Prior Reported Claim    | Independent Forensic Verdict  | Forensic Finding & Root Cause       |
+---------+----------+-------------------------+-------------------------------+-------------------------------------+
| C-01    | Native C | 142,800 EPS throughput  | Independently Reproduced      | Verified via ten_boundaries.py (B3).|
| C-02    | IPC      | 118,400 EPS in SHM v3   | Independently Reproduced      | Verified via ten_boundaries.py (B5).|
| C-03    | Pipeline | 14,800 EPS single-worker| Reproduced with Discrepancies | Measured B6 (Engine+SHM write/read).|
| C-04    | Worker   | 23,547.6 EPS 1-worker   | Reproduced with Discrepancies | Measured normalize+evaluate ONLY.   |
| C-05    | Scaling  | 30,339 EPS 8-worker     | Reproduced with Discrepancies | 1.29x speedup limited by Win spawn. |
| C-06    | Alpha    | AS-MM +$1,562.68 Net PnL| Independently Reproduced      | Realized spread exceeds maker fees. |
| C-07    | Sharpe   | AS-MM Sharpe = 44.32    | Incorrect                     | Annualization fallacy (sqrt factor).|
| C-08    | OOS      | OOS Consistency = 0.98  | Partially Verified            | Simple PnL ratio, not formal metric.|
| C-09    | Strategy | Whale Momentum profits  | Incorrect (Net Loss -$499.30) | Taker fees (5.0 bps) destroy edge.  |
| C-10    | Arbitrage| $76.09 BTC Arbitrage    | Incorrect                     | USD vs USDT basis; fees exceed spread|
| C-11    | Slippage | Zero Slippage in fills  | Incorrect                     | Top-of-book touch fill fallacy.     |
| C-12    | Ingress  | 5 Live Crypto Feeds     | Independently Reproduced      | Binance, Coinbase, Kraken, OKX, Bybit|
| C-13    | Latency  | Sub-microsecond E2E     | Unsupported                   | Pure Python E2E is 42.5 µs, not <1µs|
| C-14    | Trading  | Production Real-Money   | Unsupported / Strictly Blk    | Real-money trading strictly disabled|
+--------------------------------------------------------------------------------------------------------------------+
```

---

## 3. Forensic Analysis of Major Discrepancies

### 3.1 Claim C-03 vs. C-04: The Single-Worker Discrepancy
- **The Discrepancy**: Prior reports cited both **14,800 EPS** and **23,547.6 EPS** as the "single-worker pipeline performance".
- **Forensic Investigation**:
  - `14,800 EPS` was measured in `ten_boundaries.py` (`measure_boundary_6_end_to_end`). Its boundary encompasses:
    `raw event -> engine.step() -> FastQualityEngine -> state transition -> lineage recording -> SHMWriter.write_tick -> SHMReader.read_latest`.
  - `23,547.6 EPS` was measured in `run_hft_quantitative_comparison.py` (`run_scaling_benchmark_worker`). Its boundary encompasses:
    `raw event -> normalize(raw) -> FastQualityEngine.evaluate(canon)` **WITHOUT** state machines, SHM IPC, WAL disk append, or SQLite projections.
- **Auditor Resolution**: Both numbers are accurate for their respective scopes, but conflating them as "pipeline throughput" was misleading. We formally designate **14,800 EPS** as the **Full E2E Engine Throughput** and **23,547 EPS** as the **Stateless Normalization & Quality Ingress Rate**.

### 3.2 Claim C-05: The 8-Worker Multiprocessing Scaling Bottleneck
- **The Discrepancy**: 8 workers achieved only 30,339 EPS compared to 23,547 EPS on 1 worker (a meager 1.29x speedup on an 8-core CPU).
- **Forensic Investigation**:
  - On Windows OS, `multiprocessing.Pool` utilizes the `spawn` start method, requiring a full Python interpreter launch, dynamic library loading, and module re-importation for every worker process.
  - In short benchmark runs (10,000 events per worker), process startup overhead (~350ms per worker) dominated runtime.
  - Furthermore, each worker independently initialized synthetic event generators in memory, generating severe L3 cache and memory bus contention.

### 3.3 Claim C-07: The 44.32 Sharpe Ratio Annualization Fallacy
- **The Discrepancy**: A reported annualized Sharpe ratio of 44.32 is economically implausible in quantitative finance (Renaissance Medallion exhibits ~3.0–4.0; 44.32 indicates mathematical error).
- **Forensic Investigation**:
  - In `mdrap_strategies/tear_sheet.py` line 162:
    `sharpe = (mean_ret / std_ret) * math.sqrt(252.0 * 23400.0)`
  - The factor $\sqrt{252 \times 23400} \approx 2,428.33$ assumes returns were sampled **every single second** across a standard 6.5-hour trading day.
  - However, returns were sampled on **individual trade executions** across just ~500 seconds of simulated market time.
  - Multiplying per-trade returns by 2,428.33 artificially inflated the Sharpe ratio by a factor of 150x.
- **Auditor Resolution**: The 44.32 Sharpe ratio is **INVALIDATED as a methodological annualization error**.
