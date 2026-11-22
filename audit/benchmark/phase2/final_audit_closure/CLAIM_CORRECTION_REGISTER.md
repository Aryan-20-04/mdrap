# MDRAP Phase 2.3 — Claim Correction Register

**Audit Target:** Formal Rectification of Unsupported, Ambiguous, and Refuted Assertions  
**Evaluation Git Branch:** `main`  
**Evaluation HEAD Commit:** `51f99e8e18768860540b534297b02b507cefce05`  
**Clean Release Reference:** `maintenance/architecture-pruning-v608-clean` @ `38ec051e0958c2199268a03afb54f6f24b89cd58`  
**Auditor:** Independent Quality & Technical Certification Directorate  
**Governance Policy:** STRICTLY READ-ONLY. NO PRODUCTION DEPLOYMENT. NO CAPITAL AT RISK.

---

## 1. Register Overview

This register formally logs, classifies, and rectifies every misleading, imprecise, unsupported, or contradicted assertion identified across historical benchmark reports (`SOAK_2H_REPORT.md`, `TWO_HOUR_ACCEPTANCE.md`, `FINAL_ACCEPTANCE_REPORT.md`, `SOAK_30MIN_REPORT.md`, and related artifacts).

In accordance with institutional audit governance, **original reports remain completely unmodified to preserve the immutable audit trail**. Every finding in this register references the exact document, section, original text, classification, forensic evidence, and approved corrected wording.

### Classification Taxonomy
- **`Accurate`**: Factually sound, backed by source code and empirical measurements.
- **`Needs qualification`**: Factually sound in core concept, but lacking necessary context or scope constraints.
- **`Unsupported`**: Asserted as fact without direct empirical telemetry or mathematical proof.
- **`False or contradicted by evidence`**: Factually incorrect or disproven by direct source code trace and artifacts.

---

## 2. Comprehensive Claim Correction Table

| # | Target Document & Section | Original Claim / Text | Audit Classification | Forensic Evidence & Defect Reason | Mandatory Corrected Wording |
| :-: | :--- | :--- | :---: | :--- | :--- |
| **1** | `TWO_HOUR_ACCEPTANCE.md`<br>Section 1 (Item 5) | *"Memory progression: Initial 49.21 MB, Peak 128.90 MB, Final 38.79 MB. Net growth: -10.42 MB."* | **False or contradicted by evidence** | Conflated active running state with post-teardown idle state (line 418). Operating memory grew by $+61.06\text{ MB}$ to $+63.68\text{ MB}$ above baseline and stabilized at $\approx 110 - 113\text{ MB}$. It never ran at 38.79 MB during operation. | *"Operating memory grew from a baseline of 49.21 MB to a peak of 128.90 MB at Minute 10, stabilizing into an active steady-state of 110.27–112.89 MB (net operating expansion of +61.06 MB). Post-teardown RSS dropped to 38.79 MB after all threads and sockets stopped."* |
| **2** | `SOAK_2H_REPORT.md`<br>Section 4 (Memory) | *"The platform demonstrated negative net memory growth (-10.42 MB) during 2 hours of continuous operation."* | **False or contradicted by evidence** | Factually untrue. Net operating growth during continuous operation was $+61.06\text{ MB}$. Negative delta only exists when comparing stopped process against baseline. | *"The platform demonstrated bounded memory utilization during 2 hours of continuous operation, with operating memory stabilizing at ~110–113 MB and exhibiting no unbounded growth during this run."* |
| **3** | `TWO_HOUR_ACCEPTANCE.md`<br>Section 1 (Item 6) | *"Sub-millisecond latency profile throughout."* | **False or contradicted by evidence** | Tail percentiles exceed 1 ms ($p_{90} = 1.05\text{ ms}$, $p_{99} = 3.04\text{ ms}$, $p_{99.9} = 10.04\text{ ms}$, $\text{Max} = 99.88\text{ ms}$). Only median is sub-millisecond. | *"Sub-millisecond median latency (p50 = 476.60 µs), with 90th percentile at 1.05 ms and 99th percentile bounded at 3.04 ms."* |
| **4** | `SOAK_2H_REPORT.md`<br>Section 1 (Summary) | *"The platform demonstrated flawless reliability, complete sequence integrity, zero event loss, sub-millisecond latencies..."* | **Needs qualification** | Must explicitly qualify that sub-millisecond applies strictly to the median, not the entire tail distribution. | *"The platform demonstrated flawless transport reliability, complete broadcast sequence integrity, zero event loss, sub-millisecond median latency (p50 = 476.60 µs), and bounded tail latency..."* |
| **5** | `TWO_HOUR_ACCEPTANCE.md`<br>Section 1 (Item 4) | *"Feed gaps: 0 across all 5 venue adapters."* | **False or contradicted by evidence** | `feed_seq = 0` was passed for every event because `service.py` omitted `sequence_number` from TCP payload. `update_sequence()` requires `seq > 0`, so sequence checking was bypassed. | *"Transport stream gaps: 0 across 6,984,800 broadcast events. Venue feed sequence tracking was bypassed due to payload schema omission; upstream venue continuity remains unverified."* |
| **6** | `SOAK_2H_REPORT.md`<br>Section 3.1 | *"Upstream Feed Gaps: 0 (across Binance, Coinbase, Kraken, OKX, and Bybit)."* | **False or contradicted by evidence** | Data was 100% synthetically generated in an in-memory loop. No live exchange adapters were connected. Metric was 0 due to non-execution of sequence checks. | *"Upstream exchange feeds were not connected; synthetic event streams were tagged with venue labels. Upstream venue sequence tracking was bypassed."* |
| **7** | `TWO_HOUR_ACCEPTANCE.md`<br>Section 1 (Item 3) | *"Bijective mathematical identity matching ... Missing IDs: 0, Unexpected IDs: 0."* | **Needs qualification** | Runner executed an $\mathcal{O}(1)$ streaming invariant proof (XOR + Sum + Monotonicity); literal point-by-point set differences ($P \setminus C$) were not materialized. | *"Stream delivery integrity verified via matching XOR and arithmetic checksum invariants combined with strict monotonic sequence checking; literal set difference was omitted to prevent heap bloat."* |
| **8** | `SOAK_2H_REPORT.md`<br>Section 2.2 | *"Point-to-point bijective set reconciliation confirmed: every producer event was delivered to the consumer, and zero alien events were injected."* | **Needs qualification** | Validated via closed-form algebraic invariants and monotonic ordering ($1 \dots N$), not hash-set difference. | *"Full stream delivery confirmed via closed-form arithmetic and XOR invariants combined with continuous monotonic sequence tracking (1 to N)."* |
| **9** | `TWO_HOUR_ACCEPTANCE.md`<br>Section 1 (Item 5) | *"Observer sidecar possess zero unbounded memory leaks."* | **Unsupported** | A two-hour RSS profile cannot prove zero leaks across long-term edge cases. | *"Observer sidecar exhibited stable memory utilization with no unbounded growth observed during the two-hour test run."* |
| **10**| `SOAK_30MIN_REPORT.md`<br>Section 1 | *"The 30-minute continuous end-to-end reliability soak test was executed across the complete live pipeline."* | **Needs qualification** | Workload was synthetic; "live" refers to live-paced real-time streaming, not live market exchange connections. | *"The 30-minute soak test was executed across the complete real-time pipeline under live-paced synthetic market data ingestion."* |
| **11**| `FINAL_ACCEPTANCE_REPORT.md`<br>Section 1 | *"Production Readiness: Approved for production sidecar deployment."* | **False or contradicted by evidence** | Contradicts strict institutional governance: NO DEPLOYMENT. System is read-only infrastructure; production deployment is prohibited. | *"Production Readiness: STRICTLY PROHIBITED FROM PRODUCTION DEPLOYMENT. Authorized solely for read-only sidecar research and historical replay."* |
| **12**| `ARTIFACT_INTEGRITY.md`<br>Section 5 | *"JSONL Exporter Audit: 6,984,800 lines verified, SHA-256 recorded post-run."* | **Needs qualification** | File was verified in memory prior to process exit and unlinked on line 523; post-hoc physical file inspection from disk is impossible. | *"JSONL Exporter Audit: 6,984,800 lines were validated in-memory by the runner prior to disk cleanup. The file was unlinked post-run and is absent from disk for post-hoc verification."* |

---

## 3. Register Summary & Impact

- **Total Claims Audited:** 12 primary public claims.
- **False / Contradicted by Evidence:** 5 claims (41.7%) — all corrected above.
- **Needs Qualification:** 6 claims (50.0%) — precise technical scope added.
- **Unsupported:** 1 claim (8.3%) — downgraded to empirically verifiable bounds.
- **Audit Policy Enforcement:** No marketing hyperbole or unverified assumptions may be cited in official MDRAP documentation.
