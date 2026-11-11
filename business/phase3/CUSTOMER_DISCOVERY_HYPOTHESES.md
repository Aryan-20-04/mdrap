# MDRAP Phase 3 — Customer Discovery Hypotheses & Validation Framework

**Document Version**: 1.0.0  
**Target Product**: MDRAP — Market Data Reliability & Assurance Platform  
**Author**: Customer Discovery & Product Validation Lead (Agent 4)  
**Status**: APPROVED  

---

## 1. Purpose of the Discovery Hypotheses

Customer discovery is an empirical experiment. We do not assume that our features are automatically valuable to trading firms. We formulate **testable, falsifiable business hypotheses** and establish quantitative criteria for what constitutes validation or invalidation.

---

## 2. Core Business Hypotheses

### Hypothesis 1: The Incident Post-Mortem Cost Hypothesis
- **Hypothesis**: Mid-sized cryptocurrency market makers lose an average of **at least 8 to 24 engineering hours** of senior developer time investigating every major market-data incident because they lack deterministic tick replay and unified sequence tracking.
- **Validation Signal**: At least 7 out of 10 interviewed infrastructure engineers report spending > 1 business day diagnosing recent data outages or cross-venue desyncs.
- **Falsification Signal**: Infrastructure teams report that postmortems take $< 30\text{ minutes}$ using existing ELK/Datadog logs, or that exchange outages are so obviously binary (either 100% up or 100% down) that deep forensics are unnecessary.

### Hypothesis 2: The Read-Only Sidecar Security Hypothesis
- **Hypothesis**: Quantitative trading firms will refuse to install any third-party infrastructure software in their core network unless it is **strictly read-only, requires zero trading API keys, and has zero outbound connections**.
- **Validation Signal**: CTOs confirm that an isolated, passive sidecar model eliminates 80%+ of typical enterprise security procurement friction.
- **Falsification Signal**: Desks insist that any reliability tool must sit inline and actively block or route orders, or they require full source code escrow before running a test.

### Hypothesis 3: The Deterministic Replay Value Hypothesis
- **Hypothesis**: Quantitative researchers and traders place high value on **bit-for-bit replay of the exact order book state at tick $t$** to debug bad fills, whereas raw tick logging alone is deemed insufficient.
- **Validation Signal**: Quants confirm that inability to reconstruct exact in-memory state during volatility is a major blind spot in algorithmic backtesting and trade debugging.
- **Falsification Signal**: Quants state that standard 1-second OHLCV candles or unindexed CSV ticks from third parties are adequate for all post-trade analysis.

### Hypothesis 4: The Standalone Verification & Evidence Hypothesis
- **Hypothesis**: Firms trading significant volume on cryptocurrency exchanges have experienced disputed fills where exchange support rejected their internal logs due to lack of verifiable, tamper-proof proof.
- **Validation Signal**: At least 4 out of 10 firms cite incidents where an exchange or counterparty refused to acknowledge a fill error because internal CSV logs lacked independent cryptographic credibility.
- **Falsification Signal**: Counterparties always accept internal client logs, or exchanges have unilateral terms of service that reject all compensation claims regardless of proof.

### Hypothesis 5: The Commercial Willingness-to-Pay Hypothesis
- **Hypothesis**: Mid-sized market makers and systematic funds are willing to pay **\$1,500 to \$4,000 per month (\$18k – \$48k/year)** for an independent reliability sidecar that prevents bad-tick losses and reduces postmortem engineering fire-drills.
- **Validation Signal**: Economic buyers confirm that budget exists under "Infrastructure / Connectivity / Tooling" and that \$20k – \$40k/yr is easily approved if it saves even one senior dev week per quarter.
- **Falsification Signal**: Firms view this strictly as a trivial internal Python script that they would prefer to hack together internally rather than pay an external software license.

---

## 3. Hypothesis Tracking & Scorecard

| Hypothesis ID | Statement | Sample Size Target | Validation Metric | Status |
| :--- | :--- | :--- | :--- | :--- |
| **H-01** | Post-mortems take > 1 day without replay | 10 Interviews | $\ge 70\%$ agreement | Awaiting Interviews |
| **H-02** | Read-only zero-key sidecar unblocks adoption | 10 Interviews | $\ge 80\%$ agreement | Awaiting Interviews |
| **H-03** | Bit-for-bit BBO replay is an urgent unmet need | 10 Interviews | $\ge 60\%$ agreement | Awaiting Interviews |
| **H-04** | Merkle evidence helps resolve disputed fills | 10 Interviews | $\ge 40\%$ agreement | Awaiting Interviews |
| **H-05** | Willingness to pay \$18k–\$48k ARR | 10 Interviews | $\ge 50\%$ budget alignment | Awaiting Interviews |
