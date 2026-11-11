# MDRAP Phase 3 — Operational Cost Model & Unit Economics

**Document Version**: 1.0.0  
**Target Product**: MDRAP — Market Data Reliability & Assurance Platform  
**Author**: Pricing & Commercial Strategy Lead (Agent 7)  
**Status**: APPROVED  

---

## 1. Operating Cost Structure (Year 1 Budget)

Because MDRAP adopts a **self-hosted software model**, the core infrastructure costs (compute, NVMe storage, network bandwidth) are borne entirely by the customer. 

MDRAP's operating expenses are focused on **software maintenance, connector schema upkeep, continuous automated testing, and technical customer support**.

```
+-----------------------------------------------------------------------------------+
|                        MDRAP YEAR 1 OPERATING EXPENSE BREAKDOWN                   |
+-----------------------------------------------------------------------------------+
|  1. CLOUD CI/CD & TEST LABS         | Multi-venue WebSocket test infrastructure    |
|  2. SECURITY AUDIT & COMPLIANCE     | External code review & penetration test     |
|  3. TECHNICAL CUSTOMER SUPPORT      | SLA response & connector patch engineering  |
|  4. LEGAL & COMMERCIAL ADMIN        | Enterprise Master Services Agreement (MSA)  |
+-----------------------------------------------------------------------------------+
```

---

## 2. Detailed Annual Operating Cost Estimates

| Expense Category | Item Description | Monthly Cost | Annual Cost | Justification / Notes |
| :--- | :--- | :--- | :--- | :--- |
| **CI/CD & Testing Infrastructure** | AWS / Hetzner dedicated servers running 24/7 continuous test feeds against Binance, Coinbase, Kraken. | \$250 | \$3,000 | Essential to detect upstream exchange schema breaks before customer sidecars encounter them. |
| **Domain, Hosting & Documentation** | Marketing website, developer documentation portal, GitHub Enterprise. | \$60 | \$720 | Professional customer-facing presence and documentation. |
| **Legal & Corporate Commercials** | Enterprise software licensing agreements, mutual NDAs, corporate formation. | N/A (One-time) | \$5,000 | Clean institutional MSAs vetted by financial technology counsel. |
| **Third-Party Security Audit** | Independent static analysis & penetration audit of sidecar binary and AST checks. | N/A (Annual) | \$8,000 | Critical collateral for enterprise Infosec procurement approval. |
| **Customer Support & Tooling** | Dedicated Slack Connect channels, incident alerting (PagerDuty), ticketing. | \$150 | \$1,800 | Professional customer communication channels for SLA tracking. |
| **Total Baseline Operating Overhead** | | **~\$460 / mo** | **\$18,520 / yr** | **Extremely lean fixed baseline cost** |

---

## 3. Unit Economics per Paying Customer (Tier 2: \$36,000 ARR)

| Financial Metric | Amount (Annual) | Margin % | Notes |
| :--- | :--- | :--- | :--- |
| **Annual Contract Value (ACV)** | **\$36,000** | 100.0% | Tier 2: Market Maker Pro subscription |
| **Cost of Goods Sold (COGS)**: | | | |
| - Cloud Hosting Allocation | \$150 | 0.4% | Proportion of continuous feed monitoring lab |
| - Support Channel Licenses (Slack/Ticketing) | \$240 | 0.7% | Dedicated shared Slack channel & user seats |
| - Connector Maintenance Allocation | \$1,800 | 5.0% | ~12 engineering hours/yr allocated to venue schema patches |
| **Total Marginal COGS per Customer** | **\$2,190** | **6.1%** | |
| **Gross Profit per Customer** | **\$33,810** | **93.9%** | **Software-grade gross margins** |

---

## 4. Break-Even Analysis

With an annual fixed operating baseline of **\$18,520**:
- **Number of Tier 1 (\$18k ARR) Customers to Break Even**: **1.1 Customers**
- **Number of Tier 2 (\$36k ARR) Customers to Break Even**: **0.6 Customers** (A single Tier 2 customer fully covers annual fixed costs).
- **At 5 Tier 2 Customers (\$180,000 ARR)**:
  - Total Revenue: \$180,000
  - Total COGS: \$10,950
  - Fixed Overhead: \$18,520
  - **Net Operating Profit**: **+\$150,530 (83.6% net margin)**.

This proves that MDRAP has an exceptionally sound financial structure that does not depend on burning venture capital before achieving cash-flow positive operations.
