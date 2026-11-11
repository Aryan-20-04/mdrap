# MDRAP Phase 3 — Design Partner Pilot Agreement & Term Sheet

**Document Version**: 1.0.0  
**Target Product**: MDRAP — Market Data Reliability & Assurance Platform  
**Author**: Outreach & Customer Acquisition Lead (Agent 8)  
**Status**: STANDARD PILOT PROPOSAL TEMPLATE (NON-BINDING DRAFT)  

---

## 1. Pilot Program Objective

The objective of the MDRAP Design Partner Pilot is to evaluate the operational effectiveness, incident reconstruction speed, and cryptographic verification utility of MDRAP within an institutional trading firm’s staging or production-mirror environment.

> [!NOTE]
> This is a non-binding operational pilot framework. In strict compliance with MDRAP governance rules, no customer is represented as having signed or agreed to this offer until a formal bilateral agreement is executed.

---

## 2. Term Sheet & Pilot Scope

| Term | Specification |
| :--- | :--- |
| **Pilot Duration** | **30 Calendar Days** from completion of initial installation and smoke test. |
| **Licensing Fee** | **Zero Software License Fee (\$0.00)** during the 30-day evaluation period. |
| **Deployment Model** | **Passive Read-Only Sidecar** running on customer-provided infrastructure (Linux systemd, Docker container, or bare-metal server). |
| **Monitored Scope** | **Two (2) Customer-Selected Exchanges** (e.g. Coinbase & Kraken) across **1 to 4 designated instruments** (e.g. BTC-USD, ETH-USD). |
| **Execution Path Risk** | **Strictly ZERO**: Contains no order execution logic; requires zero private trading credentials; does not route or modify orders. |
| **Data Sovereignty** | **100% On-Premises**: All ticks, hashes, and evidence manifests remain entirely on customer hardware. Zero data transmitted externally. |

---

## 3. Mutual Operational Commitments

### 3.1 MDRAP Engineering Commitments:
1. **Turnkey Deployment Package**: Provide a pre-tested, isolated release package with automated installation and verification scripts.
2. **Dedicated Technical Onboarding**: Lead a 45-minute technical session to configure connectors and verify the 12-step smoke test.
3. **Weekly Assurance Reports**: Deliver weekly analytical digests summarizing all detected sequence gaps, clock skews, and provider telemetry.
4. **Priority Engineering SLA**: Direct Slack/Teams channel with MDRAP core engineers to investigate anomalies within 4 hours.
5. **Clean Off-Ramp**: Guarantee complete, zero-friction uninstallation in $< 5\text{ minutes}$ if customer elects not to convert.

### 3.2 Design Partner Commitments:
1. **Host Environment**: Provide a dedicated virtual machine, staging instance, or container with read-only network access to public exchange feeds.
2. **Designated Technical Liaison**: Assign an infrastructure engineer, systems engineer, or quant dev for weekly 15-minute touchpoints.
3. **Operational Feedback**: Provide candid qualitative and quantitative feedback regarding incident detection accuracy and replay utility.
4. **Commercial Decision**: Participate in an executive review at Day 25 to evaluate transitioning to an annual commercial subscription.

---

## 4. Post-Pilot Commercial Transition Terms

Upon successful completion of the 30-day pilot and satisfaction of all mutually agreed exit gates (as detailed in `PILOT_SUCCESS_CRITERIA.md`), the Design Partner receives:
- **Design Partner Discount**: 25% lifetime discount on annual software subscription tiers:
  - **Tier 1 (Essential Desk)**: \$13,500 / year (Standard: \$18,000 / year).
  - **Tier 2 (Market Maker Pro)**: \$27,000 / year (Standard: \$36,000 / year).
- **Direct Roadmap Input**: Guaranteed quarterly roadmap priority for requested exchange connectors or custom verification modules.
- **Opt-Out Right**: If the pilot fails to deliver measurable value, the partner may terminate without cost or obligation.

---

## 5. Execution & Authorization

This agreement represents a joint operational pilot commitment between:

**MDRAP Engineering Organization**  
Represented by: Lead Technical Architect  
Signature: __________________________ Date: _______________

**[Design Partner Firm Name]**  
Represented by: [CTO / Head of Infrastructure Name]  
Signature: __________________________ Date: _______________
