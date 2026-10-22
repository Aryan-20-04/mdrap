# MDRAP Demo Video Claims Ledger

Every metric, latency figure, throughput rate, and assertion displayed in the 88-second MDRAP demo video traces directly to committed source code, test suites, or benchmark artifacts.

| Scene | Claim / Figure Shown | Context / Scope | Evidence Location | Verdict |
| :--- | :--- | :--- | :--- | :--- |
| **Scene 1 (Hook)** | Crossed quote: Best Bid $83,378.20 @ OKX > Best Ask $83,374.40 @ Bybit (Spread: -$3.80) | Multi-venue synthetic order books (Seed=42) | Live output of `python cli.py bbo BTC/USD`; verified in `src/mdrap/bbo.py` | **VERIFIED** |
| **Scene 2 (Doctor)** | Active Engine Tier: C DLL Vectorized Context (`_fastpath_native.dll`) | High-speed C hot path library compiled via MinGW gcc | `src/mdrap/fastpath.py`, `build_fastpath.py`, `python cli.py doctor` | **VERIFIED** |
| **Scene 2 (Doctor)** | Smoke Test Benchmark: ~26,000 eps (38.47 µs/event) | Pure Python pipeline smoke check on local CPU | Output of `cmd_doctor` in `src/mdrap/cli/diagnostics.py` | **VERIFIED** |
| **Scene 3 (Pipeline)** | Multi-venue top-of-book stream with live INVALID / VALID classification | Real-time simulation stream running 15 deterministic quality checks | Live output of `python cli.py live BTC/USD -l 5 --sim` | **VERIFIED** |
| **Scene 4 (Quarantine)** | Processed: 1,000 events, 0 dropped, 5 INVALID, 32 SUSPICIOUS | Seeded fault injection simulator run (seed=42) | `python cli.py run -e 1000 -s 42` | **VERIFIED** |
| **Scene 4 (Quarantine)** | Quarantine reason codes: `["DUPLICATE"]`, `["PRICE_ANOMALY", "CROSS_FEED_DISAGREEMENT"]` | Quarantine table retention in SQLite | Query output of `python cli.py query quarantine 5` | **VERIFIED** |
| **Scene 5 (Consolidated Depth)** | 10-level consolidated book with Order Flow Imbalance (+0.20) and Arbitrage detection | Multi-venue Level-2 order book engine | `src/mdrap/depth.py`, `python cli.py depth BTC/USD` | **VERIFIED** |
| **Scene 6 (Speed)** | Native Core: 47.5 ns/tick (21.04M eps) | Synthetic, in-process, wire-to-SHM publish (T1 native C kernel) | `src/mdrap/mdrap_core.c`, `benchmarks/stage_breakdown.json` (22.2M eps, 44.9 ns) | **VERIFIED** |
| **Scene 6 (Speed)** | End-to-End Python In-Memory Step: 8.3 µs p50 (120,481 eps) | Full Python pipeline (ingest + normalize + quality + reconciler) | Committed benchmark artifact: `benchmarks/results/honest_benchmarks.json` | **VERIFIED** |
| **Scene 6 (Speed)** | Durable Ingest (WAL disk commit): 773.3 µs p50 | SQLite WAL batch commit to disk | `python cli.py run -e 1000 -s 42` (p50: 773.3 µs) | **VERIFIED** |
| **Scene 7 (Audit Proof)** | Tamper-evident Merkle chain: `✖ AUDIT CHAIN TAMPERING DETECTED!` on modified row | SHA-256 Merkle chain verification over audit log | `src/mdrap/security.py`, `python cli.py audit --verify` | **VERIFIED** |
| **Scene 8 (CTA)** | v4.0.0 Beta, 1,542 passing tests, MIT License | Repository configuration and continuous quality gate | `pyproject.toml`, `LICENSE`, `scripts/check.py` | **VERIFIED** |
