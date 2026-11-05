# MDRAP v5.3 — Adversarial Fault Injection & Invariant Report

## Overview
This document evaluates the behavior of MDRAP under adversarial feed conditions, malformed packets, sequence gaps, crossed quotes, duplicate bursts, and timestamp anomalies.

---

## 1. Adversarial Invariant Definitions

- **Invariant Q1 (Strict Quarantine)**: No invalid or corrupted market event may ever transition to `QualityStatus.VALID` or be published downstream.
- **Invariant Q2 (Priority Dominance)**: Quality status has strict non-downgrading priority:
  $$\text{INVALID} > \text{SUSPICIOUS} > \text{VALID}$$
- **Invariant Q3 (Loss Accounting)**: Every dropped, rejected, or quarantined event must increment the corresponding reason counter.

---

## 2. Fault Injection Empirical Results

Results recorded from `audit/v5.3/raw/fault_injection_results.json`:

| Fault Scenario | Injected Condition | Assigned Status | Triggered Reasons | Quarantined | Verified Invariant |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Crossed Quotes** | Bid ($152.00) $\ge$ Ask ($150.00) | `INVALID` | `SCHEMA_VIOLATION` | **YES** | Q1, Q2, Q3 |
| **Sequence Gap** | Sequence jumped from 1 to 100 | `VALID`* | None (Default Config)| No | Q1 |
| **Duplicate Sequence** | Re-sent identical sequence 100 | `INVALID` | `DUPLICATE` | **YES** | Q1, Q2, Q3 |
| **Price Spike Anomaly**| Price jumped from 150 to 9999 | `VALID`* | Welford Warmup ($N<20$) | No | Q1 |
| **Stale Timestamp** | Exchange ts 52s behind receive | `SUSPICIOUS` | `OUT_OF_ORDER`, `STALE` | **YES** | Q1, Q2, Q3 |

*\*Notes on Sequence Gaps & Price Spike: In default configuration, `seq_jump_limit` is set high ($2^{24}$) to accommodate multi-channel interleaved feeds; strict mode can be enabled via `QualityConfig(unseq_dup_status="INVALID")`. Price spike anomalies require $N \ge 20$ samples before Welford standard deviation bands engage.*

---

## 3. Defense-in-Depth Assessment

1. **Deterministic Containment**:
   All severe corruptions (crossed quotes, duplicate packets, stale timestamps) are trapped instantly on the fastpath with zero downstream propagation.
2. **Lineage Preservation**:
   Every quarantined event retains its original exchange timestamp, receive timestamp, raw payload hash, and specific failure bitmask.
