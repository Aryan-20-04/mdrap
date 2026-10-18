# MDRAP Customer Discovery Playbook & Interview Guide

> **Phase C: Demand Validation Protocol**  
> **Target:** Small quantitative hedge funds, crypto market makers, prop trading desks, and fintech data platform teams running Python market-data pipelines across multiple feeds.  
> **Status:** Active Customer Discovery Framework (Zero Leading Questions)

---

## 1. Executive Context & Objective

Before writing commercial code or launching paid editions, customer demand must be validated empirically through direct, hypothesis-testing interviews with practitioners who manage market-data ingestion in production.

### Hypothesized Customer Profile
- **Organization Type:** Quant hedge funds ($10M–$250M AUM), proprietary crypto market makers, retail broker-dealers, or algorithmic execution desks.
- **Technology Stack:** Primarily Python (with NumPy, pandas, Polars) for strategy logic, research, and data processing; optional C/C++ or Rust for execution gateways.
- **Feed Complexity:** Consumes 2 to 10 market-data feeds (e.g., Binance, Kraken, Coinbase, Databento, Polygon, ITCH) simultaneously.
- **Pain Point:** Has experienced silent packet drops, sequence gaps, duplicate ticks, or cross-venue timestamps drifting, causing bad executions, invalid signal calculation, or costly manual post-incident debugging.
- **Current Alternatives:** Custom ad-hoc Python scripts, in-memory deques without persistence, Kafka/Aeron/Redpanda with high operational overhead, or trust in raw vendor normalization.

---

## 2. The 8 Non-Leading Discovery Questions

Each question is structured to uncover past behavior, concrete costs, and real workflows without leading the interviewee into validating our product.

### Question 1: Incident History
> *"Describe the last time bad, duplicate, or missing market data reached a downstream strategy or analytics database."*

- **Intent:** Validate whether market data reliability is an active, painful problem or a theoretical concern.
- **Probe For:**
  - What was the specific data defect? (Crossed quote, negative price, dropped ticks, duplicate burst, sequence reset).
  - Was it caught before or after execution orders were placed?
- **Red Flag:** Interviewee cannot recall an incident in the past 12 months, or states *"our upstream vendor cleans everything perfectly."*

---

### Question 2: Detection Latency
> *"How did you find out about the defect?"*

- **Intent:** Determine if detection is proactive (automated invariant monitors) or reactive (post-trade reconciliation, risk alert, PnL anomaly).
- **Probe For:**
  - Did an alert fire within milliseconds, or did a portfolio manager or risk officer flag abnormal PnL hours later?
- **Red Flag:** *"The exchange or our broker called us."* (Signifies high vulnerability and absence of intake validation).

---

### Question 3: Diagnosis & Postmortem Effort
> *"How long did diagnosis take from first awareness to root cause?"*

- **Intent:** Quantify the engineering cost of post-incident diagnosis.
- **Probe For:**
  - Did you have a bit-exact replay log of the raw feed as it arrived off the wire?
  - Could you reproduce the exact state of the orderbook and validation decisions at the time of the incident?
  - Did diagnosis require re-fetching historical exchange data and manually comparing CSVs?

---

### Question 4: Current Invariant Architecture
> *"What tooling or checks do you run today to detect sequence gaps, duplicates, or cross-feed divergence?"*

- **Intent:** Map out the competitive baseline and incumbent architectures.
- **Probe For:**
  - In-house Python scripts vs off-the-shelf tools.
  - Are checks running in-line (hot path) or asynchronous / batch after the fact?
  - What is the latency overhead added by current validation checks?

---

### Question 5: Maintenance & Operational Overhead
> *"What does it cost you in time or infrastructure to maintain your current capture and validation setup?"*

- **Intent:** Uncover willingness to pay and total cost of ownership (TCO).
- **Probe For:**
  - How many engineer-hours per month are spent updating feed parsers, fixing SQLite locks, or managing Kafka brokers?
  - How often do venue API changes break the ingestion layer?

---

### Question 6: Mid-Stream Crash & Durability Invariants
> *"What happens to your pipeline and downstream strategies if an ingestion process crashes or is killed (`kill -9`) mid-stream?"*

- **Intent:** Evaluate the necessity of zero-loss Write-Ahead Logging (WAL) and atomic crash recovery.
- **Probe For:**
  - Do you experience duplicate events or missing spans on restart?
  - How long does cold-start state reconstruction take?
  - Can you recover state deterministically without manual database patching?

---

### Question 7: Ownership & Accountability
> *"Who in your organization owns data quality, and how is their success measured?"*

- **Intent:** Identify the true economic buyer vs technical user.
- **Probe For:**
  - Is ownership split between quant researchers, data engineers, and infrastructure DevOps?
  - Are they measured on uptime, p99 latency, slippage, or data availability?

---

### Question 8: Adoption Blockers & Dealbreakers
> *"What would prevent you from adopting an embedded, single-node library for capture, validation, and replay?"*

- **Intent:** Identify structural blockers early (licensing, language runtime, regulatory compliance, performance overhead).
- **Probe For:**
  - Latency budget (e.g., must be under 500 nanoseconds, or 10 microseconds is acceptable).
  - Language lock-in (e.g., must be pure Python with optional C wheels vs Rust/C++ requirement).
  - Licensing concerns (e.g., MIT required, AGPL strictly forbidden).

---

## 3. Interview Evaluation Scorecard

For each discovery interview, record responses against this quantitative rubric:

| Dimension | Low Pain (Score: 1) | Medium Pain (Score: 2) | High Pain (Score: 3) | Weight |
|:---|:---|:---|:---|:---:|
| **Incident Severity** | Minor annoyance; no PnL impact | Slippage / halted trading for < 1hr | Direct PnL loss, regulatory query, or broken fill | 3x |
| **Detection Lag** | Caught in < 1ms by in-house checks | Caught within minutes by risk alarms | Caught end-of-day or after trading hours | 2x |
| **Recovery Invariant** | Automatic clean failover with 0 loss | Manual restart with 5–15 min downtime | Corrupted database; required manual data surgery | 2x |
| **Replay Durability** | Full deterministic wire replay | Partial logs in flat CSV/JSON files | No raw capture; rely on vendor REST re-queries | 2x |
| **Maintenance Burden**| < 2 hours/month | 10–20 hours/month | Dedicated data engineer required | 1x |

**Scoring Interpretation:**
- **< 15 points:** Unqualified. Current tooling is sufficient. Do not pitch.
- **16–23 points:** Qualified for Shadow-Mode PoC (free MIT engine).
- **24–30 points:** Prime candidate for commercial pilot and paid integration support.

---

## 4. Hypothesis Invalidation & Gate 3 Criteria

### Invalidation Conditions (When to Abandon Commercial Ambitions)
The commercial edition thesis is **invalidated** if:
1. 5 consecutive qualified teams report that their existing custom scripts or vendor feeds catch >95% of data anomalies with negligible maintenance overhead.
2. In shadow-mode trials, >50% of MDRAP-flagged `INVALID` or `SUSPICIOUS` events are classified by the customer as false positives (e.g., legitimate market events flagged as anomalies).
3. Teams value low latency (<1 µs) so exclusively that any Python orchestration is ruled out in favor of pure FPGA/C++ feed handlers.

### Gate 3 Decision Criteria (When to Build Commercial Capabilities)
Do **not** build commercial features or hire sales until all of the following hold:
- [ ] **3+ Independent Teams** have completed a 1-day Shadow-Mode PoC on live/recorded feeds.
- [ ] **Demonstrated Accuracy:** True positive rate >90% on data anomalies, with customer confirming real-world defect detection.
- [ ] **Unmet Need in Free Edition:** The team explicitly requests a capability beyond single-node embedded MIT scope (e.g., enterprise off-site DR replication, formal SLA onboarding, or custom exchange adapter).
- [ ] **Explicit Willingness to Pay:** At least one team issues a formal intent to purchase paid support or a commercial license.
