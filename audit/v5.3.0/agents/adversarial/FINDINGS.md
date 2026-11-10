# Independent Adversarial Reviewer — Detailed Findings

## Executive Summary
Audited all public claims against ground-truth source code, benchmark outputs, and operational telemetry. While MDRAP contains genuine, high-quality C and Python market data processing algorithms, the repository exhibits significant over-claiming, benchmark data inflation, version dissociation, committed platform-specific binary blobs, mock-heavy test inflation, and a 32-second "24-hour soak test".

## Categorized Findings

### CRITICAL
1. **F-ADV-01: Version Dissociation (4.0.1 Codebase vs 5.3.0 Claims)**:
   - Package is hardcoded to `4.0.1` across `_version.py`, `pyproject.toml`, `/health` REST endpoint, and `claims_manifest.json`, while documentation touts `v5.3.0`.
   - Users installing from PyPI or git receive version `4.0.1`.

2. **F-ADV-02: Benchmark Reproducibility & Fabrication Failures**:
   - `README.md` claims `python benchmarks/bench_scale.py` reproduces the 43.67M EPS Native C kernel. It actually runs a Python `Engine.submit()` benchmark (~20k–50k EPS).
   - In `benchmarks/stage_breakdown.json:49-56`, p50, p95, p99, and max for `native_c_kernel` are all identical (22.89 ns). The 45.0 ns p99 in `README.md` was manually fabricated.
   - Raw committed benchmark data in `audit/v5.3/raw/benchmarks_raw.json` records Native C at **416.3k EPS** and Python at **149.9k EPS** (2.78x speedup). `README.md` inflates these to **510.8k EPS** and **170.1k EPS** (3.00x).

3. **F-ADV-03: Committed Platform-Specific Binaries in Git**:
   - `src/mdrap/_fastpath_native.dll` and `src/mdrap/mdrap-core.exe` are committed directly to the repository, posing supply-chain security risks.

### HIGH
4. **F-ADV-04: The 32.71-Second "24-Hour Continuous Soak Test"**:
   - `docs/audit/v5/24H_SOAK_TEST_REPORT.md:16-19` openly notes the "24-hour soak" ran for only **32.71 seconds** (1,032 events total, 1 event for Kraken), with 24-hour stability mathematically extrapolated.

5. **F-ADV-05: Aspirational Blueprints Labeled as Production Features**:
   - `af_xdp.py` contains no kernel bypass; it is an in-memory `bytearray` simulator.
   - `multicast_arbitrator.py` contains no multicast UDP sockets; it is an in-memory queue simulator.
   - `consensus.py` header notes it is an in-process simulation not for multi-host consensus.
   - `kafka_sink.py` contains no Kafka driver dependencies and defaults to `InMemoryKafkaProducer`.

6. **F-ADV-06: Disk Durability "Always" Claim Overstated**:
   - `README.md` advertises 21,615 EPS for "Always" durability. In reality, true tick-by-tick synchronous fsync achieves only 270 EPS. The 21.6k figure reflects batch-100 writes.

### MEDIUM
7. **F-ADV-07: Core Edition Boundaries Blurred**:
   - Matching Engine, Avellaneda-Stoikov MM, and 14 Risk Gates are advertised in `README.md` as core capabilities, but reside in `packages/mdrap-strategies`.
8. **F-ADV-08: Dead Code in Core**:
   - 779 lines of dead code in `trading_cli.py`, legacy `BinaryJournal` in `journal.py`, and `scheduler.py` are unreferenced or superseded.
9. **F-ADV-09: Hardcoded Demo Credentials in Load Simulator**:
   - `src/mdrap/workload_simulator.py:49` hardcodes `auth_token: str = "mdrap_demo_pro_key"`.\n