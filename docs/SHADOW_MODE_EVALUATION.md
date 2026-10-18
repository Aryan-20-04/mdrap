# MDRAP Shadow-Mode PoC Evaluation Guide

> **Phase C: Production Shadow-Mode Verification Protocol**  
> **Purpose:** Run MDRAP non-intrusively in parallel with an existing market-data ingestion pipeline on 1 recorded trading day to evaluate defect detection, false-positive rate, latency, and operational footprint without trading risk.

---

## 1. Overview & Principles

The Shadow-Mode Proof of Concept (PoC) is designed to give engineering leadership empirical evidence of MDRAP's value before committing to any code migration or license purchase.

### Core Principles
1. **Zero Production Risk:** MDRAP runs strictly as a sidecar or offline replay process; it does not sit in the execution path during the PoC.
2. **Ground Truth Comparison:** MDRAP's classifications (`VALID`, `SUSPICIOUS`, `INVALID`) are cross-referenced with your existing alerts, trade logs, and post-trade reconciliation reports.
3. **Objective Classification:** Every divergence is categorized as:
   - **True Positive (TP):** MDRAP detected a real feed defect that harmed or risked downstream systems (e.g. crossed quote, negative price, dropped ticks).
   - **False Positive (FP):** MDRAP flagged a benign, legitimate market event.
   - **Missed Anomaly (FN):** An anomaly occurred that MDRAP failed to catch.
   - **New Discovery:** A previously unnoticed feed flaw that MDRAP revealed for the first time.
4. **Time-Bounded:** The trial takes less than 1 business day of engineering time.

---

## 2. Evaluation Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│                      SHADOW EVALUATION WORKFLOW                        │
├────────────────────────────────────────────────────────────────────────┤
│                                                                        │
│   Recorded Feed Stream (Raw JSONL / PCAP / CSV from 1 Trading Day)     │
│                              │                                         │
│              ┌───────────────┴───────────────┐                         │
│              ▼                               ▼                         │
│    Existing Pipeline / Log          MDRAP Shadow Engine                │
│    (Downstream Database)            (Engine + IngestLog WAL)           │
│              │                               │                         │
│              ▼                               ▼                         │
│    Existing Anomaly Tags             MDRAP Quality Decisions           │
│              │                               │                         │
│              └───────────────┬───────────────┘                         │
│                              ▼                                         │
│                 MDRAP Shadow Evaluator                                 │
│                 (scripts/shadow_poc.py)                                │
│                              │                                         │
│                              ▼                                         │
│                 PoC Scorecard & Discrepancy Log                        │
│                 (Precision, Recall, FP Rate, Throughput)               │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Step-by-Step Execution Guide

### Step 1: Export 1 Day of Raw Market Data
Export raw tick records from your capture network or broker feed. Records should include the raw message payload, source venue, and receive timestamp:
```jsonl
{"source": "BINANCE", "receive_ts": 1710000000.123, "payload": {"instrument": "BTC-USDT", "event_type": "TRADE", "exchange_ts": 1710000000.120, "sequence": 1001, "price": 68500.0, "quantity": 0.5}}
{"source": "BINANCE", "receive_ts": 1710000000.125, "payload": {"instrument": "BTC-USDT", "event_type": "QUOTE", "exchange_ts": 1710000000.122, "sequence": 1002, "bid": 68500.0, "ask": 68498.0}}
```

### Step 2: (Optional) Prepare Baseline Anomaly Tags
If your team has existing incident logs or alerts for that day, create an optional `baseline_tags.json`:
```json
{
  "1002": {
    "is_anomaly": true,
    "reason": "Crossed market detected by risk engine"
  }
}
```

### Step 3: Run the Shadow PoC Evaluation Harness
Execute the evaluation using the provided script:
```bash
python scripts/shadow_poc.py \
    --input data/recorded_feed_2026_10_15.jsonl \
    --baseline data/baseline_tags.json \
    --output audit/shadow_poc_report.md \
    --json-output audit/shadow_poc_metrics.json
```

### Step 4: Review Results & Scorecard
Open `audit/shadow_poc_report.md` to inspect the generated confusion matrix, anomaly breakdown, and sample discrepancies.

---

## 4. Key Metrics & Qualification Thresholds

| Metric | Target Threshold | Interpretation |
|:---|:---:|:---|
| **Precision** | > 90% | High proportion of flagged alerts represent genuine market defects. |
| **Recall** | > 95% | Almost zero defects escape detection into downstream systems. |
| **False Positive Rate** | < 2% | Minimal alarm fatigue for operations and trading desks. |
| **Throughput** | > 100,000 eps | Easily keeps pace with multi-venue burst conditions. |
| **Time to First Validated Event** | < 20 ms | Instant startup latency for cold starts and process restarts. |

---

## 5. Post-PoC Decision Framework (Gate 3)

At the conclusion of the 1-day shadow trial, the engineering team and sponsor convene to review the scorecard:

1. **Adopt Free MIT Edition:**
   - If the metrics demonstrate superior data cleansing, deterministic replay, and zero data loss, the team can immediately embed `mdrap.Engine` and `IngestLog` into production under the permissive MIT license.
2. **Qualify for Commercial Pilot (Gate 3):**
   - If the team requires capabilities outside single-node embedded scope (e.g. multi-site offsite WAL replication, customized proprietary venue decoders, or 24/7 mission-critical SLA onboarding), they qualify for Gate 3 commercial onboarding.
3. **Invalidation:**
   - If >50% of MDRAP alerts are determined to be benign market events that the customer rejects as defects, the deployment is discontinued and anomaly thresholds are revised.
