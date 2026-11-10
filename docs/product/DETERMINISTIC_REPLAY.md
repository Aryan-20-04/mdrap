# MDRAP Deterministic Incident Replay Engine

**Document Version**: 2.0  
**Responsible Agent**: Agent 5 (Deterministic Incident Replay)  
**Status**: VERIFIED IMPLEMENTATION  

---

## 1. Architectural Mission

MDRAP's most critical operational capability is **Deterministic Incident Replay**.
When an execution anomaly occurs, a quantitative trader or risk officer must be able to ask:
*"Reconstruct the BTC/USD order book between 10:32:15 and 10:32:16, show what our system actually saw, and prove why the strategy received an unexpected state."*

The replay engine guarantees that replaying a recorded incident multiple times produces the **exact same tick sequence, state transitions, quality decisions, and SHA-256 state digest**.

---

## 2. Replay Architecture & Pipeline

```text
+-------------------------------------------------------------------------------+
|                             HISTORICAL INCIDENT STORE                         |
|   • DataQualityIncident (incident_id: "inc-1794334791-0001")                  |
|   • Time Window: [start_ts, end_ts]  |  Instrument: "BTC/USD"                 |
+---------------------------------------+---------------------------------------+
                                        |
                                        v
+-------------------------------------------------------------------------------+
|                    CHRONOLOGICAL RAW EVENT RETRIEVAL                          |
|   • Exact historical raw payloads loaded from SQLite / IngestLog              |
|   • Strict tie-breaking sort: (exchange_ts, sequence_num, source, event_id)   |
+---------------------------------------+---------------------------------------+
                                        |
                                        v
+-------------------------------------------------------------------------------+
|                       VIRTUAL CLOCK EXECUTION STEP                            |
|   • Isolated VirtualClock locks system time to historical receive timestamps   |
|   • Bypasses wall-clock drift and external timing dependencies                |
+---------------------------------------+---------------------------------------+
                                        |
                                        v
+-------------------------------------------------------------------------------+
|                   QUALITY RE-EVALUATION & STATE RECONSTRUCTION                |
|   • Re-execute QualityEngine.evaluate() -> Re-detect exact anomaly trigger    |
|   • Re-execute BBOEngine.observe() -> Reconstruct order book evolution        |
+---------------------------------------+---------------------------------------+
                                        |
                                        v
+-------------------------------------------------------------------------------+
|                     CRYPTOGRAPHIC EQUIVALENCE VERIFICATION                    |
|   • Compute SHA-256 state hash of replayed book & decisions                   |
|   • Match against original execution hash (100% bitwise parity)               |
|   • Mark incident as "reproduced: true" with reproduction_hash in DB          |
+-------------------------------------------------------------------------------+
```

---

## 3. Python API & CLI Interface

### CLI Execution
```powershell
# Reconstruct incident and print forensic timeline
python cli.py replay --incident inc-1794334791-0001 --db data/mdrap.db

# Output structured JSON proof
python cli.py replay --incident inc-1794334791-0001 --db data/mdrap.db --json
```

### Python SDK Usage
```python
from mdrap.replay import IncidentReplayEngine

engine = IncidentReplayEngine(db_path="data/mdrap.db")
result = engine.replay_incident("inc-1794334791-0001")

print(f"Replayed {result.events_replayed} events.")
print(f"Reproducible: {result.is_reproducible}")
print(f"Reproduced Anomalies: {result.reproduced_anomalies}")
print(f"State Hash: {result.replayed_state_hash}")
```

---

## 4. Replay Acceptance Verification

The replay engine is verified by running multiple independent folds across identical recorded inputs:
- **Test Command**: `pytest tests/test_replay.py -v`
- **Criteria**:
  1. Identical number of replayed events.
  2. Identical quality status and reason tags on every tick.
  3. Identical best bid, best ask, and crossed-book flags at each step.
  4. Bitwise identical SHA-256 state hash across independent runs.
