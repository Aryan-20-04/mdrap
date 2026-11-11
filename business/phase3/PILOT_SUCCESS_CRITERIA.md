# MDRAP Phase 3 — Pilot Success Criteria & Exit Gates

**Document Version**: 1.0.0  
**Target Product**: MDRAP — Market Data Reliability & Assurance Platform  
**Author**: Lead Product Analytics & Metrics Engineer (Agent 6)  
**Status**: APPROVED  

---

## 1. Pilot Graduation Framework

The 30-day Design Partner Pilot is governed by strict, transparent exit gates. Both the customer engineering team and MDRAP engineers agree on these criteria prior to initial installation.

```
+-----------------------------------------------------------------------------------+
|                        MDRAP PILOT GRADUATION STAGES                              |
+-----------------------------------------------------------------------------------+
|  GATE 1: DEPLOYMENT & SMOKE TEST (Day 1)   | Sub-45 min setup; 12-step demo passes|
|  GATE 2: ZERO-INTERFERENCE STABILITY (Wk 1)| 7 days uptime; 0 crashes; <50µs lat  |
|  GATE 3: REAL-WORLD ANOMALY CAPTURE (Wk 2) | Catches >= 1 genuine exchange event  |
|  GATE 4: INCIDENT REPLAY ACCURACY (Wk 3)   | Bit-for-bit replay; verified proof   |
|  GATE 5: COMMERCIAL CONVERSION (Wk 4)      | Willingness to pay documented        |
+-----------------------------------------------------------------------------------+
```

---

## 2. Quantitative Exit Criteria (Must Meet All 5 to Graduate)

### Criterion 1: Seamless Non-Interfering Operation
- **Requirement**: MDRAP operates continuously for 30 days without causing a single CPU spike ($> 15\%$), memory leak ($> 200\text{ MB}$ RSS growth), or interfering with customer trading execution.
- **Pass Threshold**: 99.95% sidecar uptime; zero trading engine latency regressions.

### Criterion 2: Accurate Incident Detection
- **Requirement**: MDRAP detects and logs all exchange sequence drops, clock skews, and unannounced feed stalls occurring during the 30-day window.
- **Pass Threshold**: At least one confirmed exchange anomaly caught that customer's existing monitoring failed to flag promptly; false positive rate $< 0.1\%$.

### Criterion 3: Deterministic Replay & Investigation Speed
- **Requirement**: When an operational incident occurs, the customer engineering team reconstructs the event using `mdrap replay` in $< 15\text{ minutes}$.
- **Pass Threshold**: Customer confirms that replay reconstructed the exact order book state at tick $t$ without manual log aggregation.

### Criterion 4: Standalone Evidence Verification
- **Requirement**: Customer's compliance or risk lead successfully verifies an exported incident manifest using the zero-dependency verification utility (`tools/verify_evidence.py`).
- **Pass Threshold**: 100% cryptographic proof validation; zero internal MDRAP library dependencies required.

### Criterion 5: Economic Value Realization
- **Requirement**: The customer's CTO or Infrastructure Lead formally confirms that MDRAP delivered measurable operational value (in hours saved or prevented bad fills) justifying ongoing commercial deployment.
- **Pass Threshold**: Mutual execution of an annual commercial subscription agreement or formal agreement on paid pilot extension.

---

## 3. Failure & Off-Ramp Protocol

If a pilot fails any technical criteria:
- **Root Cause Analysis (RCA)**: Completed within 48 hours.
- **Immediate Rollback**: MDRAP is cleanly stopped and uninstalled in $< 5\text{ minutes}$ with zero lingering files or customer dependencies.
- **No Lock-in**: All collected SQLite databases and evidence packages remain customer property.
