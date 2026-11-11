# Interactive Product Demonstration Guide

This guide describes how to run and interpret the MDRAP interactive product demonstration.

MDRAP provides two operational demonstration modes:
- **Mode A: Local Deterministic Demonstration** (`--mode local`): 100% offline, self-contained, reproducible test against multi-venue market data containing an injected sequence discontinuity.
- **Mode B: Live Public Feed Demonstration** (`--mode live`): Connects strictly in read-only mode to public WebSocket endpoints (Coinbase and Kraken). If no external network connectivity is present, it automatically falls back to Mode A.

---

## 1. Quickstart

To run the local deterministic demonstration:

```bash
python tools/demo/run_demo.py --mode local
```

To run with an interactive pause between every step (for presentations and technical evaluations):

```bash
python tools/demo/run_demo.py --mode local --interactive
```

To run the live public exchange demonstration:

```bash
python tools/demo/run_demo.py --mode live
```

---

## 2. The 15 Workflow Steps (Operational Invariants)

The demonstration programmatically executes and validates 15 core architectural invariants:

1. **Dual Venue Initialization**: Activates passive monitoring for `SYNTHETIC_BINANCE` and `SYNTHETIC_COINBASE` BTC-USD feeds. Verifies that zero order execution or routing components are loaded.
2. **Stream & Ingest**: Normalizes raw wire messages into canonical `L1-BBO` events and calculates microsecond timestamp vectors.
3. **Provider Telemetry**: Displays health status, clock skew ($<5\text{ms}$), frame counts, and zero-stale metrics.
4. **Sequence Number Tracking**: Tracks monotonic sequence counters per venue.
5. **Cryptographic Event Provenance**: Computes SHA-256 payload hashes and lineage digests binding the raw frame to the canonical state.
6. **15-Rule Deterministic Quality Engine**: Evaluates spread crossing, price jumps, stale ticks, clock skew, and boundary checks.
7. **Controlled Sequence Gap Injection**: Injects a controlled sequence discontinuity (sequence jumping from 1003 to 1250, simulating 246 dropped packets during exchange load spikes).
8. **Anomaly Quarantine & Routing Isolation**: Flags event as `SUSPICIOUS`, tagging with reason `SEQUENCE_GAP` and alerting downstream consumers to isolate pricing.
9. **Persistent Incident Creation**: Creates an institutional `DataQualityIncident` with unique timestamped ID, severity `HIGH`, venue, and impact summary.
10. **RFC 6962 Merkle Tree Anchoring & WAL Commit**: Collects event lineage hashes into a cryptographic Merkle tree using RFC 6962 domain separation (`0x00` leaf, `0x01` node) and commits to SQLite WAL.
11. **Application Teardown / Crash Simulation**: Closes active database connections and discards all in-memory buffers to simulate a host failure.
12. **Restart Recovery & Verification**: Reopens the SQLite database from disk, recovers the incident state, and verifies that the lineage hash matches bit-for-bit.
13. **Deterministic Incident Replay**: Invokes `IncidentReplayEngine` across the incident time corridor, reproducing the exact sequence gap and producing a deterministic state digest.
14. **Standalone External Evidence Verification**: Exports `evidence_<incident_id>.json` and invokes the standalone verifier (`tools/verify_evidence.py`), confirming mathematical authenticity without internal dependencies.
15. **Incident Post-Mortem Report**: Generates a forensic incident markdown report (`postmortem_<incident_id>.md`) for compliance and risk teams.

---

## 3. Interpreting Output Artifacts

Running the demonstration produces three artifacts in the output directory (default: `demo/output/`):

### 1. `demo_local.db`
A standard SQLite database operating in WAL mode. Contains:
- `canonical_events`: Ingested and normalized market data.
- `provenance`: Cryptographic records including `payload_sha256` and `lineage_hash`.
- `incidents`: Forensic anomaly records including severity and trigger event IDs.

### 2. `evidence_inc-*.json`
A standalone forensic evidence package conforming to the MDRAP external assurance schema:
```json
{
  "evidence_type": "INCIDENT_FORENSIC_RECORD",
  "incident_id": "inc-1700000001-0001",
  "event_id": "evt-cd75491fa9b66a44-7",
  "provider": "SYNTHETIC_BINANCE",
  "instrument": "BTC-USD",
  "data_mode": "SIMULATION",
  "timestamps": { ... },
  "quality": { "status": "SUSPICIOUS", "reasons": ["SEQUENCE_GAP"] },
  "integrity": {
    "raw_id": "demo-raw-gap-1250",
    "payload_sha256": "...",
    "lineage_hash": "...",
    "merkle_root": "...",
    "merkle_proof": [ ... ]
  },
  "raw_wire": "{ ... }"
}
```

### 3. `postmortem_inc-*.md`
An executive and technical post-mortem report detailing the anomaly timeline, root cause, cryptographic verification results, and operational invariants.

---

## 4. Standalone Verification

Any external party, auditor, or compliance officer can independently verify the exported evidence package using standard Python with zero dependencies:

```bash
python tools/verify_evidence.py demo/output/evidence_inc-*.json
```

Output:
```text
[VERIFIED] Evidence package is mathematically authentic and untampered.
RFC 6962 Merkle proof successfully verified against root anchor.
```
