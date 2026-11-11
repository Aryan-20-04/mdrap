# MDRAP Phase 3 — Customer Discovery Interview Results Template

**Document Version**: 1.0.0  
**Target Product**: MDRAP — Market Data Reliability & Assurance Platform  
**Author**: Customer Discovery & Product Validation Lead (Agent 4)  
**Status**: APPROVED TEMPLATE  

---

## 1. Interview Metadata

- **Company Name**: 
- **Company Segment**: (Market Maker / Quant Prop / Hedge Fund / Gateway)
- **Interviewee Name & Title**: 
- **Date & Time (UTC)**: 
- **Interviewer**: 
- **Interview Duration**: (e.g. 30 mins)
- **Recording / Notes Link**: 

---

## 2. Infrastructure Profile & Tech Stack

- **Number of Active Exchanges / Venues**: 
- **Primary Exchanges**: (e.g., Binance, OKX, Bybit, Coinbase, Deribit, CME)
- **Ingestion Architecture**: (Direct WebSocket / FIX / Third-party Vendor)
- **Programming Languages for Feed Handlers**: (C++ / Rust / Python / Go)
- **Hosting Environment**: (Equinix Bare Metal / AWS / GCP / Hybrid)
- **Current Observability Stack**: (Datadog / Prometheus / Grafana / Custom ELK)

---

## 3. Qualitative Interview Responses

### Section A: Current Pains & Sequence Handling
- *How do they currently handle sequence gaps and silent drops?*
  - **Summary**: 
  - **Direct Quotes**: 
- *How do they monitor feed freshness and zombie connections?*
  - **Summary**: 

### Section B: Recent Market Data Incidents
- *Description of the last significant incident*: 
- *Time to root-cause determination*: 
- *Direct financial or operational impact*: 
- *Was it possible to deterministically replay the event?*: 

### Section C: Dispute Resolution & Evidence
- *Have they disputed a trade or fill with an exchange?*: 
- *What evidence did they provide?*: 
- *What was the outcome?*: 

### Section D: Reaction to MDRAP Value Proposition
- *Initial reaction to read-only independent reliability sidecar*: 
- *Reaction to sub-50 µs overhead and bit-for-bit deterministic replay*: 
- *Reaction to standalone RFC 6962 Merkle proof verifier*: 
- *Biggest objection or adoption blocker*: 

---

## 4. Commercial & Pilot Evaluation

- **Open to 30-Day Read-Only Design Partner Pilot?**: (YES / NO / MAYBE)
- **Key Conditions for Pilot Participation**: 
- **Stated Willingness to Pay (Annual/Monthly)**: 
- **Decision Making Unit (Who approves budget?)**: 
- **Follow-Up Action & Timeline**: 

---

## 5. Hypothesis Scorecard (1 to 5 Scale)

| Hypothesis | Score (1 = Strongly Refuted, 5 = Strongly Confirmed) | Qualitative Justification |
| :--- | :--- | :--- |
| **H-01 (Postmortem Cost)** | [ ] | |
| **H-02 (Read-Only Safety)** | [ ] | |
| **H-03 (Deterministic Replay)** | [ ] | |
| **H-04 (Forensic Evidence)** | [ ] | |
| **H-05 (Willingness to Pay)** | [ ] | |
