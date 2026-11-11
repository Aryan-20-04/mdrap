# Getting Started with MDRAP

Welcome to the **Market Data Reliability & Assurance Platform (MDRAP)** — an institutional-grade, read-only sidecar engineered to convert noisy, fragmented, delayed, and duplicate market data into a validated, canonical, and cryptographically auditable stream.

MDRAP sits directly upstream of pricing engines, risk monitors, algorithmic execution clusters, and compliance data stores, providing continuous passive assurance without introducing order-routing risks.

---

## 1. Core Architectural Concepts

1. **Read-Only Non-Intrusive Tap**: MDRAP is designed exclusively as an observer. It never holds API execution secrets, never exposes order submission endpoints, and cannot modify live order queues.
2. **Normalized Canonical Model**: Raw WebSocket frames (Coinbase, Kraken, Binance, Databento) are parsed into standardized `CanonicalEvent` structures with monotonic microsecond vector timestamps.
3. **15-Rule Deterministic Quality Engine**: Every incoming tick is evaluated against 15 strict market microstructure invariants (spread crossing, sequence continuity, clock drift, price spikes, zero volume, etc.).
4. **Cryptographic Provenance**: Every normalized tick receives an immutable SHA-256 payload digest and a chained lineage hash linking wire data directly to validation outcomes.
5. **Durable Forensic SQLite WAL**: Anomalies, telemetry, and evidence trees are persisted immediately using SQLite Write-Ahead Logging (WAL) for microsecond recovery during host failures.
6. **RFC 6962 Merkle Trees**: Ingestion batches and incident corridors are anchored into Merkle trees enabling zero-knowledge inclusion proofs verified by external auditors with zero dependencies.

---

## 2. Quick 5-Minute Tour

### Step 1: Run Pre-Flight Validation
Ensure your host environment satisfies all prerequisites:
```bash
python deploy/validate_environment.py
```

### Step 2: Run the Offline Synthetic Demonstration
Run the 15-step interactive demonstration using a realistic multi-venue dataset:
```bash
python tools/demo/run_demo.py --mode local
```

You will see:
- Continuous ingestion of simulated Binance and Coinbase BTC-USD feeds.
- Real-time connector telemetry and monotonic sequence tracking.
- Injection of a controlled 246-packet sequence jump anomaly.
- Automated quarantine and creation of a persistent `DataQualityIncident`.
- RFC 6962 Merkle tree construction and atomic WAL commit.
- Simulated host teardown and immediate WAL recovery.
- Deterministic incident replay producing a bit-for-bit identical state digest.
- Independent standalone verification of exported evidence.

### Step 3: Run Live Feed Observation (Read-Only)
If your host has public internet connectivity, observe live public exchange feeds:
```bash
python tools/demo/run_demo.py --mode live
```

### Step 4: Verify Forensic Evidence Standalone
Verify the exported incident evidence package using the standalone validator:
```bash
python tools/verify_evidence.py demo/output/evidence_inc-*.json
```

---

## 3. Next Steps

- Review [Installation Guide](installation.md) to set up MDRAP via wheel, source, or container.
- Review [Architecture Guide](architecture.md) to examine the internal data pipeline.
- Review [Quality Engine](quality-engine.md) to understand the 15 deterministic anomaly rules.
- Review [Self-Hosted Deployment](self-hosted-deployment.md) for production sizing and systemd deployment.
