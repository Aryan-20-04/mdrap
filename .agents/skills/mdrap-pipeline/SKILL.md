---
name: mdrap-pipeline
description: >-
  Run, benchmark, profile, and troubleshoot the MDRAP market data pipeline.
  Use when executing live runs, benchmarks, load tests, chaos drills, profiling bottlenecks,
  or querying canonical data, lineage, and health metrics.
---

# MDRAP Pipeline Operation & Benchmarking Skill

This skill provides step-by-step procedures for running, testing, and debugging the Market Data Reliability & Acceleration Platform (MDRAP).

## 1. Quick Verification & Testing
Before making any commits or evaluating changes, run the automated test suite:
```bash
python -m pytest tests/ -v
```
All 14 tests must pass. Ensure tests complete in under 5 seconds.

---

## 2. Running Live & Headless Pipelines

### Live Terminal Dashboard (interactive inspection)
```bash
python cli.py run --events 100000 --dashboard
```
- Refreshes at 8 Hz with low overhead.
- Inspect real-time throughput, p50/p95/p99 latency, and quality status breakdown.

### Headless Pipeline (emits JSON metrics summary)
```bash
python cli.py run --events 200000 --db data/mdrap.db
```

---

## 3. Benchmarking & Ground-Truth Scoring

Run a controlled benchmark that scores fault detection against ground truth:
```bash
python cli.py benchmark --events 500000 --seed 42 --label baseline_v1
```
Output is automatically written to `benchmarks/<label>_<timestamp>.json`.

### Profiling Bottlenecks (cProfile)
```bash
python cli.py benchmark --events 100000 --seed 42 --profile --label profile_run
```
Generates `benchmarks/<label>_profile.prof`. Inspect with Snakeviz if needed:
```bash
snakeviz benchmarks/profile_run_profile.prof
```

---

## 4. Load Testing

Discover the sustainable throughput limit by executing load sweeps:
```bash
python cli.py loadtest --levels 10000,50000,100000,250000,500000
```
Inspect latency degradation and throughput plateaus across levels.

---

## 5. Chaos & Resilience Testing

Simulate feed outages to verify gap detection and reliability score penalties:
```bash
python cli.py chaos --kill-source FEEDX --kill-start 1000 --kill-duration 500
```
Verify that FEEDX reliability drops and the reconciler seamlessly favors alternative feeds.

---

## 6. Querying Stored Market Data

Query SQLite state created by pipeline runs:
```bash
# Latest quote/trade for an instrument
python cli.py query --latest AAPL

# Inspect complete lineage and decision reasons for an event
python cli.py query --lineage <event_id>

# View source reliability scores and performance metrics
python cli.py query --health

# Inspect quarantined records
python cli.py query --quarantine 10
```
