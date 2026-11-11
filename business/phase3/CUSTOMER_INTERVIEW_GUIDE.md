# MDRAP Phase 3 — Customer Discovery Interview Guide

**Document Version**: 1.0.0  
**Target Roles**: Chief Technology Officer (CTO), Head of Quantitative Infrastructure, Senior Market Data Engineer, Quantitative Developer  
**Author**: Customer Discovery & Product Validation Lead (Agent 4)  
**Status**: APPROVED FOR USER RESEARCH SESSIONS  

---

## 1. Interview Protocol & Conduct Rules

To ensure objective, unbiased customer discovery:
1. **Never pitch the product in the first 25 minutes**. Listen to their existing pain, current architecture, and operational frustrations.
2. **Never ask leading questions** (e.g. *"Would you love a tool that automatically fixes sequence gaps?"*). Instead ask: *"How do you currently handle it when an exchange WebSocket silently skips 50 sequence numbers?"*
3. **Seek concrete historical evidence** (e.g. *"Tell me about the last time an incident happened..."*) rather than theoretical opinions.
4. **Distinguish complaints from budget-backed priorities**. If an issue is annoying but has never lost them money or consumed senior developer time, it is not an urgent commercial problem.

---

## 2. Structured 30-Minute Interview Script

### Block 1: Warm-Up & Current Infrastructure Architecture (7 Minutes)
*Goal: Map their connectivity scale, complexity, and current feed handler design.*

1. **Exchange Connectivity**: *"Roughly how many exchanges or liquidity venues does your firm connect to concurrently?"*
2. **Feed Technology**: *"Are you connecting directly via exchange public/private WebSockets and FIX, or do you consume through a consolidated market-data vendor (like Databento, CoinAPI, Kaiko)?"*
3. **Normalization Architecture**: *"How are incoming market-data feeds normalized into your internal trading engine format? Is that handled in-process within each strategy engine, or via a centralized gateway service?"*
4. **Sequence & Gap Handling**: *"How does your current stack identify when a feed has dropped messages or experienced an unannounced sequence gap?"*
5. **Freshness & Staleness**: *"How do your systems detect when a feed is nominally connected but has stopped updating prices (stale book / zombie connection)?"*
6. **Order Book Validation**: *"What checks currently ensure that your reconstructed local order book hasn't inverted (crossed book where bid > ask) due to a missed cancellation?"*

---

### Block 2: Incident Post-Mortems & Operational Reality (10 Minutes)
*Goal: Uncover the real cost, time, and emotional frustration of market-data incidents.*

7. **The Last Bad Incident**: *"Think back to the last time an unexpected market-data glitch, outage, or anomaly impacted your trading desk. What happened?"*
8. **Root Cause Timeline**: *"How long did it take from the moment the anomaly occurred until your engineering team had absolute certainty about the root cause?"*
9. **Blame & Attribution**: *"When that incident occurred, how did you prove whether the fault was on the exchange's side, your hosting provider's network, or an internal software race condition?"*
10. **Wire Event Retention**: *"Do you currently retain the exact raw wire bytes (or exact WebSocket frames) of incoming feeds, or do you only store the normalized internal data structures?"*
11. **Reproducibility & Replay**: *"If your head trader asks you today: 'Replay exactly what the order book looked like on Venue X at 14:02:11 UTC last Thursday,' can you reproduce that state bit-for-bit?"*
12. **Audit Evidence**: *"What concrete evidence do you currently retain to defend your desk against a disputed fill or to demonstrate execution quality to an institutional LP or regulator?"*

---

### Block 3: Tooling Gaps & In-House Limitations (6 Minutes)
*Goal: Evaluate existing alternatives and identify where current solutions fall short.*

13. **Monitoring Stack**: *"What monitoring, logging, or observability tools are currently running in your production environment (e.g., Datadog, Prometheus, Grafana, custom scripts)?"*
14. **What's Missing**: *"Where do those general monitoring tools fall short when it comes to low-level market data microstructure and tick anomalies?"*
15. **Replay Capabilities**: *"Have you built an internal replay harness? If so, what was the hardest part about maintaining it across frequent exchange schema updates?"*
16. **Dispute Resolution**: *"When an exchange or broker fills you at an off-market price, what is your standard process for disputing the fill? How often do you successfully win compensation?"*

---

### Block 4: Commercial Validation & Adoption Friction (7 Minutes)
*Goal: Uncover security requirements, procurement hurdles, and willingness to pay.*

17. **Sidecar Positioning**: *"If an independent reliability tool existed that ran as a local, read-only sidecar beside your feed handlers—validating sequence gaps, clock skews, and preserving replayable Merkle evidence with under 50 microseconds overhead—what would be your immediate reaction?"*
18. **Security & Deployment Boundaries**: *"What strict security requirements would such a tool have to satisfy before you would allow it inside your server environment? (e.g., zero outbound network connections, zero access to order-routing keys, open-source auditability)?"*
19. **Adoption Blockers**: *"What would be the single biggest reason you would decide NOT to install such a sidecar, even if it worked as advertised?"*
20. **Design-Partner Pilot**: *"We are currently selecting three quantitative trading desks for a 30-day, zero-cost, restricted read-only design partner pilot on 2 of your existing feeds. Would your team be open to testing this alongside your current setup?"*
21. **Willingness to Pay**: *"Assuming the pilot runs for 30 days and catches at least one sequence drop or stale book that your current systems missed—saving hours of postmortem work—what would a reasonable annual subscription be for your firm?"*
22. **Procurement Authority**: *"Who at your firm ultimately approves new infrastructure software licenses (CTO, Managing Partner, Head of Quant Infra)?"*

---

## 3. Post-Interview Debrief Checklist

Immediately following each interview, the interviewer must document:
- [ ] Was the pain around market data incidents rated as a Top 3 priority or merely an annoyance?
- [ ] Does the firm currently have an in-house team maintaining feed handlers, or are they under-resourced?
- [ ] Did they cite a specific financial loss from a recent data incident?
- [ ] What is their reaction to a read-only, non-intrusive sidecar?
- [ ] Is there an invitation to schedule a technical demonstration?
