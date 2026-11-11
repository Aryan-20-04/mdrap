# MDRAP Phase 3 — Commercial Pricing Strategy & Packaging

**Document Version**: 1.0.0  
**Target Product**: MDRAP — Market Data Reliability & Assurance Platform  
**Author**: Pricing & Commercial Strategy Lead (Agent 7)  
**Status**: APPROVED PRICING HYPOTHESIS  

---

## 1. Commercial Pricing Principles

1. **Value-Aligned, Not Cost-Plus**: We price based on the financial losses prevented (bad fills, disputed trades) and developer hours saved, not the marginal cost of software delivery.
2. **Transparent Tiering by Infrastructure Complexity**: Pricing scales with the number of concurrently monitored exchange feeds and instruments, matching the customer's operational scale.
3. **Low Adoption Friction for Early Adopters**: 30-day zero-cost pilot leading into an annual subscription with quarterly billing options.
4. **No Consumption Metering on Raw Ticks**: We do **not** charge per gigabyte or per million ticks. Metered tick pricing discourages customers from monitoring all feeds. We charge a predictable flat subscription per instance/seat.

---

## 2. Packaging Tiers & Pricing Hypotheses

```
+---------------------------------------------------------------------------------------------------------+
|                                         MDRAP COMMERCIAL TIERS                                          |
+------------------------------------+------------------------------------+-------------------------------+
|  TIER 1: ESSENTIAL DESK            |  TIER 2: MARKET MAKER PRO (CORE)   |  TIER 3: ENTERPRISE GATEWAY   |
|  $1,500 / month ($18,000 / year)   |  $3,000 / month ($36,000 / year)   |  $6,000 / month ($72,000 / yr)|
+------------------------------------+------------------------------------+-------------------------------+
| - Up to 3 Exchange Connections     | - Up to 10 Exchange Connections    | - Unlimited Exchange Feeds    |
| - Up to 10 Instruments             | - Up to 50 Instruments             | - Unlimited Instruments       |
| - 14-Day Ingest WAL Retention      | - 60-Day Ingest WAL Retention      | - Custom Retention & Storage  |
| - Deterministic Replay Engine      | - Deterministic Replay Engine      | - Dedicated High-Throughput   |
| - RFC 6962 Standalone Verification | - RFC 6962 Standalone Verification | - Custom FIX/SBE Connectors   |
| - Community & Email Support (Next) | - Priority Slack Support (4h SLA)  | - 24/7 Dedicated SRE SLA (1h) |
| - Self-Hosted Single Node          | - Multi-Node Co-Location Support   | - Multi-Region Co-Location    |
+------------------------------------+------------------------------------+-------------------------------+
```

---

## 3. Customer Buyer Tier Alignment

### Archetype A: Small Proprietary Trading Desk (1–5 Traders)
- **Target Tier**: **Tier 1: Essential Desk (\$18,000 ARR)**
- **Typical Setup**: Trading 2–3 venues (e.g. Binance Spot, Binance Futures, OKX).
- **Buyer Persona**: Managing Partner / Senior Trader.
- **Budget Window**: \$1,000 – \$2,000/month tool budget; easily approved on corporate credit card or simple invoice.
- **Value Realization**: Prevents one \$10,000 bad fill $\to$ Immediate annual ROI.

### Archetype B: Growing Crypto Market Maker (15–60 Employees) [CORE TARGET]
- **Target Tier**: **Tier 2: Market Maker Pro (\$36,000 ARR)**
- **Typical Setup**: Active continuous quoting across 6–10 exchanges (Binance, Coinbase, OKX, Bybit, Kraken, Deribit).
- **Buyer Persona**: Head of Quantitative Infrastructure / CTO.
- **Budget Window**: \$30,000 – \$60,000 infrastructure software budget.
- **Value Realization**: Saves 200+ senior developer hours annually in postmortem fire-drills and exchange disputes (\$25k+ dev salary saved) + eliminates crossed-book fills.

### Archetype C: Institutional Execution Platform / Multi-Strat Hedge Fund (60+ Employees)
- **Target Tier**: **Tier 3: Enterprise Gateway (\$72,000+ ARR)**
- **Typical Setup**: Institutional smart order router (SOR) or execution management system (EMS) monitoring 15+ global liquidity pools.
- **Buyer Persona**: Chief Technology Officer / Head of Platform.
- **Budget Window**: \$60,000 – \$150,000 vendor line-item.
- **Value Realization**: Standalone Merkle evidence packages provide third-party regulatory compliance proof and resolve high-value institutional LP execution disputes.

---

## 4. Contract Terms & Payment Mechanics

- **Billing Frequency**: Annual contracts billed annually upfront (5% discount) or quarterly in advance.
- **Pilot Transition**: 30-day evaluation pilot converts automatically into an annual subscription unless cancelled in writing before Day 30.
- **Add-On Services**:
  - Custom Exchange Connector Engineering: \$5,000 one-time setup fee per bespoke proprietary venue.
  - Co-Location Deployment Assistance (Equinix LD4 / NY4 setup): \$7,500 one-time onboarding.
