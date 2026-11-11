# MDRAP Phase 3 — Commercial & Product Risk Register

**Document Version**: 1.0.0  
**Target Product**: MDRAP — Market Data Reliability & Assurance Platform  
**Author**: Independent Red-Team Commercial Auditor (Agent 9)  
**Status**: APPROVED RISK MATRIX  

---

## 1. Risk Matrix Overview

This register identifies the commercial, technical, legal, and operational risks facing MDRAP during Phase 3 customer discovery and design partner acquisition.

```
       5 |                           [RSK-01]     [RSK-02]
       4 |              [RSK-04]                  [RSK-03]
IMPACT 3 |                                        [RSK-05]
       2 | [RSK-07]     [RSK-06]
       1 |
         +--------------------------------------------------
           1       2       3         4            5
                           LIKELIHOOD
```

---

## 2. Risk Register Ledger

| Risk ID | Category | Risk Description | Likelihood | Impact | Severity Score | Actionable Mitigation Strategy |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **RSK-01** | Commercial | **In-House "Build vs. Buy" Rejection**: Quant infrastructure teams refuse external software, believing they can replicate features internally. | 4 (High) | 5 (Critical) | **HIGH (20)** | Position strictly as an independent third-party auditor/assurance layer (a team cannot objectively audit their own code); emphasize time-to-value (<45 min setup). |
| **RSK-02** | Operational | **Exchange Schema Churn Overload**: Upstream exchange API changes cause connector failures, creating support debt for a lean engineering team. | 5 (Very High) | 5 (Critical) | **CRITICAL (25)** | Implement automated CI canary monitors (`tools/run_live_observation.py`) that run continuous 24/7 ingestion tests against live exchanges to catch breaks before clients. |
| **RSK-03** | Infosec | **Enterprise Security & Network Vetting Friction**: Institutional funds reject closed binaries or require extensive SOC 2 audits before deployment. | 4 (High) | 4 (High) | **HIGH (16)** | Provide complete read-only AST proofs (`tools/check_read_only_safety.py`); open-source the standalone verifier; mandate unprivileged systemd execution with zero inbound ports. |
| **RSK-04** | Market | **Niche TAM Ceiling in Pure Crypto**: Total mid-tier crypto market maker market is ~450 firms, limiting total annual revenue to < \$16M ARR. | 3 (Moderate) | 4 (High) | **MEDIUM (12)** | Dominate the high-margin crypto market maker niche in Year 1–2, then expand core assurance sidecar into TradFi FX, commodities, and US equity options in Phase 5. |
| **RSK-05** | Legal / Counterparty | **Exchanges Ignore Third-Party Dispute Proofs**: Venues refuse to reimburse bad fills despite mathematically verifiable Merkle proofs due to adhesive Terms of Service. | 4 (High) | 3 (Moderate) | **MEDIUM (12)** | Focus value pitch primarily on *internal incident postmortem velocity* (saving dev days) and *preventative algorithm circuit breaking*, treating exchange dispute recovery as an upside bonus. |
| **RSK-06** | Key Person | **Single-Developer Maintenance Bottleneck**: Product support and feature updates depend excessively on a single core developer. | 2 (Low) | 4 (High) | **MEDIUM (8)** | Standardize documentation, operations runbooks, and automated test harnesses; allocate revenue from the first 2 paying customers to retain a secondary support engineer. |
| **RSK-07** | Performance | **Customer Hardware Contention Regressions**: Sidecar resource usage causes microsecond jitter on co-located trading engines under extreme volume bursts. | 1 (Rare) | 5 (Critical) | **LOW (5)** | Enforce strict CPU core pinning (`taskset -c`), AVX2 native C hot path, zero-allocation ring buffers, and bounded-memory tombstones. |

---

## 3. Monitoring & Review Cadence

The Lead Orchestrator and Commercial Team will review this Risk Register on a **bi-weekly basis** throughout the customer discovery and pilot onboarding phase. Any risk that transitions from Likelihood $\le 2$ to $\ge 4$ triggers an immediate strategy realignment.
