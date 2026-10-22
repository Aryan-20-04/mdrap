# MDRAP Demo Video — Shot List & Voiceover Script

- **Target Duration**: 88 Seconds (75–90s Range)
- **Format**: 16:9 1080p (1920x1080), 30 fps
- **Pacing**: ~140 words per minute (Conversational, technical, engineer-to-engineer)
- **Primary Message**: *"Never trade on corrupted, crossed, stale, or phantom market data, and be able to prove what you rejected and why."*

---

## Shot List & Script Breakdown

### Scene 1: The Hook (0:00 – 0:06 | 6 seconds)
- **Visual**: Dark terminal window showing `python cli.py bbo BTC/USD`. Crossed market highlighted in vivid red: Best Bid `$83,378.20` @ OKX vs Best Ask `$83,374.40` @ Bybit (Spread: `-$3.80`).
- **On-Screen Caption**: `One bad tick can cost a strategy real money.`
- **Lower-Right Scope**: `Scope: Synthetic NBBO | Simulation seed=42`
- **Voiceover**:
  > *"One bad tick can cost a trading strategy real money. When exchanges desynchronize and quotes cross, execution algos buy phantom liquidity."*
- **Word Count**: 20 words

---

### Scene 2: Install & Diagnostics (0:06 – 0:14 | 8 seconds)
- **Visual**: `pip install -e .` followed by `python cli.py doctor`. Checklist shows all green: C DLL Vectorized Context (`_fastpath_native.dll`), Native Core Binary (`mdrap-core.exe`), Windows QPC ~100ns precision, and 26k eps smoke benchmark.
- **On-Screen Caption**: `One command to verify your environment.`
- **Lower-Right Scope**: `Scope: Diagnostics & Acceleration Verification`
- **Voiceover**:
  > *"Meet MDRAP: an open-source market data reliability and acceleration sidecar. In one command, mdrap doctor verifies your acceleration tiers and precision clocks."*
- **Word Count**: 23 words

---

### Scene 3: Multi-Venue Live Pipeline (0:14 – 0:30 | 16 seconds)
- **Visual**: Real-time multi-venue market connector (`python cli.py live BTC/USD -l 5 --sim`). Top-of-book telemetry shows simultaneous feeds from FEEDX, FEEDY, and FEEDZ, with live wire latency and deterministic quality tags (`INVALID` vs `VALID`).
- **On-Screen Caption**: `Multi-venue feeds in, one canonical stream out.`
- **Lower-Right Scope**: `Scope: Real-Time Multi-Venue Feed Ingestion & Validation`
- **Voiceover**:
  > *"It sits right before your algos. Multi-venue raw feeds stream in — from crypto websockets to direct exchange feeds. MDRAP normalizes, aligns sequence numbers, and validates every tick in real time."*
- **Word Count**: 29 words

---

### Scene 4: Catching Bad Data & Quarantine (0:30 – 0:45 | 15 seconds)
- **Visual**: Seeded simulation run (`python cli.py run -e 1000 -s 42`) followed by `python cli.py query quarantine 2`. Shows 1,000 processed events, zero silent drops, and exact JSON quarantine entries with explicit reason codes (`["DUPLICATE"]`, `["PRICE_ANOMALY", "CROSS_FEED_DISAGREEMENT"]`).
- **On-Screen Caption**: `Bad data is quarantined, never silently dropped. Every rejection has a reason code.`
- **Lower-Right Scope**: `Scope: Deterministic Quality Engine (15 Rules) | SQLite Quarantine`
- **Voiceover**:
  > *"MDRAP enforces fifteen deterministic quality rules. Bad data is never silently discarded — it's quarantined with explicit reason codes, preserving full lineage and auditability."*
- **Word Count**: 24 words

---

### Scene 5: Consolidated BBO & Depth (0:45 – 0:55 | 10 seconds)
- **Visual**: Consolidated 10-level order book (`python cli.py depth BTC/USD`). Displays aggregated depth across Binance, Coinbase, Kraken, OKX, and Bybit, alongside Order Flow Imbalance (OFI: +0.20) and cross-exchange arbitrage detection.
- **On-Screen Caption**: `Consolidated NBBO across venues.`
- **Lower-Right Scope**: `Scope: Consolidated Level-2 Depth Ladder & Microstructure`
- **Voiceover**:
  > *"You get consolidated Level-2 depth, micro-price telemetry, and cross-venue arbitrage detection without writing custom book-building logic."*
- **Word Count**: 17 words

---

### Scene 6: Speed, Honestly (0:55 – 1:08 | 13 seconds)
- **Visual**: Live execution of native hot-path C binary (`python cli.py core -e 1000000`) clocking 21.04 million events/sec at 47.5 ns/tick. Split comparison against committed honest benchmarks: 8.3 µs p50 in Python memory, 773 µs for durable disk WAL commits.
- **On-Screen Caption**: `Native core: 44.8–61.3 ns/tick (synthetic, wire-to-SHM). Python pipeline: ~15 µs.`
- **Lower-Right Scope**: `Scope: Standalone C Core vs End-to-End Pipeline`
- **Voiceover**:
  > *"Performance, honestly disclosed: the standalone native C core hits 47 nanoseconds per tick wire-to-shared-memory. The full Python pipeline handles twelve thousand events a second in-memory with sub-millisecond durable WAL commits."*
- **Word Count**: 32 words

---

### Scene 7: Cryptographic Merkle Audit Proof (1:08 – 1:20 | 12 seconds)
- **Visual**: `python cli.py audit --verify` passes (`✔ CRYPTOGRAPHIC AUDIT VERIFICATION PASSED`). Then, an attacker tampers with row 1 in SQLite (`python tools/demo/tamper.py`). Re-verifying immediately triggers red alert: `✖ AUDIT CHAIN TAMPERING DETECTED!`.
- **On-Screen Caption**: `Change one record and the proof breaks.`
- **Lower-Right Scope**: `Scope: SHA-256 Merkle Chain | Cryptographic Lineage Proof`
- **Voiceover**:
  > *"When compliance or risk asks what happened, every event traces to a SHA-256 Merkle chain. If anyone tampers with a historical row, the mathematical proof breaks instantly."*
- **Word Count**: 26 words

---

### Scene 8: Call to Action (1:20 – 1:28 | 8 seconds)
- **Visual**: Clean summary screen with repository URL (`github.com/Aryan-20-04/mdrap`), `pip install mdrap`, 1,542 tests badge, MIT License, and v4.0.0 Beta designation.
- **On-Screen Caption**: `Open source. Try it in 5 minutes.`
- **Lower-Right Scope**: `Scope: v4.0.0 Beta | MIT License`
- **Voiceover**:
  > *"MDRAP is open source, MIT licensed, and backed by fifteen hundred passing tests. Clone it and run it in five minutes."*
- **Word Count**: 20 words

---

## Cumulative Summary
- **Total Duration**: 88.0 seconds
- **Total Voiceover Words**: 191 words (~130 words per minute average)
- **Audio Delivery**: Crisp, natural, engineer-to-engineer cadence
