# Weekly Design Partner Incident Review Agenda

**Meeting Cadence**: Weekly (30 Minutes)  
**Attendees**: Design Partner Lead Engineer, Risk Lead, MDRAP Core Support Engineer  
**Objective**: Review recorded data-quality anomalies, analyze incident corridors, and tune detection thresholds.

---

## 1. Weekly Reliability Scorecard
- **Monitoring Window**: From `[YYYY-MM-DD]` to `[YYYY-MM-DD]`
- **Monitored Venues**:
- **Total Ticks Ingested**:
- **Valid Ticks**: ________ (______%)
- **Suspicious Anomalies**: ________ (______%)
- **Invalid Ticks Quarantined**: ________ (______%)

---

## 2. Anomaly Breakdown
| Anomaly Category | Incident Count | Venues Affected | Max Severity |
| :--- | :--- | :--- | :--- |
| **Sequence Gaps** | | | |
| **Crossed / Inverted Books** | | | |
| **Excessive Spreads** | | | |
| **Stale Ticks** | | | |
| **Clock Skew ($>50\text{ms}$)** | | | |
| **Exchange Disconnections** | | | |

---

## 3. Deep-Dive on Top Incident
- **Incident ID**: `inc-____________________`
- **Venue & Symbol**:
- **Detection Timestamp**:
- **Root Cause Analysis**:
  - Did the exchange matching engine drop packets or delay cancel orders?
  - Was there cross-venue arbitrage dislocation during this window?
- **Replay Verification**:
  - Replay State Digest: `____________________________________`
  - Bit-for-bit reproducibility verified: `[ ] YES` | `[ ] NO`
- **Impact on Trading Desk**:
  - Did downstream trading models experience execution slippage?
  - Was the anomaly successfully isolated before order execution?

---

## 4. Threshold Tuning & Next Steps
- Action Items:
  - `[ ] Adjust clock drift threshold for feed ______`
  - `[ ] Add new monitored instrument: ______`
  - `[ ] Export Merkle evidence package for audit archives`
