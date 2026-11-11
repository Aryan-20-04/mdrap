# MDRAP Phase 3 — Ideal Customer Profile (ICP) Specification

**Document Version**: 1.0.0  
**Target Product**: MDRAP — Market Data Reliability & Assurance Platform  
**Author**: Lead Product Strategist & Market Research Lead (Agent 1)  
**Status**: APPROVED FOR DESIGN PARTNER TARGETING  

---

## 1. Executive Summary

MDRAP is not a general-purpose trading platform, a consumer market terminal, or a commercial market data reseller. It is an **independent, zero-interference reliability, validation, and forensic assurance sidecar** positioned directly between upstream market-data feeds and latency-sensitive trading logic.

Because MDRAP operates strictly read-only and focuses on stream validation, cryptographic provenance, sequence-gap detection, and deterministic incident replay, our Ideal Customer Profile (ICP) must be sharply defined around firms that suffer **direct financial loss, operational downtime, or regulatory risk** from silent market-data corruption.

---

## 2. Ideal Customer Profile (Primary Archetype)

### Company Profile: "The Agile Multi-Exchange Digital Asset Market Maker"
- **Firm Type**: Quantitative Proprietary Trading Desk / Crypto Liquidity Provider / Institutional Market Maker.
- **Firm Size**: 15 – 150 employees (Engineering / Quantitative Dev team: 5 – 35 engineers).
- **Assets Under Management (AUM) / Capital**: \$15M – \$250M active working trading capital.
- **Daily Volume**: \$50M – \$1.5B across spot, perpetuals, and futures.
- **Connectivity Footprint**: Actively connected to 5 to 15 cryptocurrency exchanges (e.g., Binance, Coinbase, OKX, Bybit, Kraken, Deribit) via WebSocket and FIX feeds.
- **Infrastructure Architecture**: Co-located bare-metal servers (Equinix LD4, TY3, NY4) or hybrid AWS/GCP cloud instances running custom C++ or Python execution engines.

---

## 3. Organizational Roles & Key Stakeholders

| Stakeholder Role | Primary Responsibilities | Direct Pain with Market Data | MDRAP Value Proposition | Stance / Buying Influence |
| :--- | :--- | :--- | :--- | :--- |
| **Chief Technology Officer (CTO)** | Overall system uptime, engineering velocity, technical risk, vendor spend. | Repeated engineering fire-drills after unexpected exchange outages or feed desyncs. | Eliminates tribal-knowledge postmortems; provides turnkey, provable root-cause evidence. | **Economic Buyer** (Signs contract / budget approval). |
| **Head of Quantitative Infrastructure** | Feed handlers, low-latency gateways, normalization, connectivity stability. | Maintaining custom parsers across 10+ exchange APIs that break without notice; sequence gap disputes. | Pre-built passive sidecar with zero execution footprint, deterministic replay, and RFC 6962 audit trails. | **Primary Champion & User** (Installs and runs pilot). |
| **Lead Quantitative Researcher / Trader** | Strategy PnL, pricing models, inventory management, alpha research. | Model fills on bad ticks, phantom arbitrage spikes, crossed books triggering false circuit breakers. | Reliable quality status (`VALID`, `SUSPICIOUS`, `INVALID`); clean training data without corrupted anomalies. | **Influencer** (Demands cleaner data feeds). |
| **Compliance & Risk Officer** | Regulatory audits, best execution evidence, dispute resolution with venues. | Inability to independently prove what data was received when a venue claims an order was executed against a stale book. | Independent, mathematically verifiable SHA-256 wire digests and external Merkle proof packages. | **Gatekeeper** (Approves forensic security). |

---

## 4. Operational & Technical Triggers (Why Buy Now?)

A prospect enters the active buying window when they experience one of three specific operational events:
1. **The "Phantom Arbitrage" PnL Shock**: A trading algorithm detects a cross-venue price discrepancy that turns out to be a delayed or dropped tick from Venue A. The algo sends two legs, only to find Venue A's price was stale by 250ms, resulting in adverse selection and immediate slippage loss (\$10k – \$150k in seconds).
2. **The Exchange Disconnect Post-Mortem Dispute**: An exchange WebSocket disconnects during extreme volatility, drops 400 packets upon reconnecting, and resumes without proper sequence reset indicators. The firm spends 3 days of senior dev time analyzing raw packet dumps to determine whether the firm's network or the exchange dropped the packets.
3. **Internal Feed Handler Maintenance Exhaustion**: The internal infrastructure team is overwhelmed maintaining in-house feed normalization and gap-detection scripts across frequent exchange API schema changes, distracting them from core alpha and execution engine optimization.

---

## 5. Disqualification Criteria (Who NOT to Target)

Targeting the wrong customer wastes limited early-stage startup bandwidth. Prospects matching any of the following must be **disqualified**:

1. **Pure Retail Traders / Hobbyists**:
   - *Reason*: No budget for infrastructure software; trade through retail UI or simple REST APIs.
2. **Mega-Tier Tier-1 Investment Banks (e.g. Goldman Sachs, Citadel Securities)**:
   - *Reason*: 18-to-24-month procurement cycles, mandatory enterprise master service agreements (MSAs), in-house proprietary hardware (FPGAs) that will not adopt a third-party early-stage sidecar.
3. **Single-Exchange Single-Asset Traders**:
   - *Reason*: Lack multi-venue reconciliation and cross-venue divergence challenges.
4. **Firms Demanding Out-of-the-Box Order Execution / OMS Replacement**:
   - *Reason*: Violates MDRAP's non-negotiable read-only safety boundary.

---

## 6. The Three Most Promising Customer Segments

From market analysis, three distinct segments exhibit immediate alignment with MDRAP:

1. **Segment 1 (Primary Target)**: Mid-Sized Cryptocurrency Market Makers & HFT Liquidity Desks.
2. **Segment 2 (High Potential)**: Multi-Venue Systematic Digital Asset Hedge Funds & Quantitative Prop Desks.
3. **Segment 3 (Strategic Expansion)**: Institutional Crypto Execution Gateways, Brokerage APIs & Infrastructure Providers.
