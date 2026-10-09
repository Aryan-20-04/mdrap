# Phase 5 Source Reconciliation & Lineage Audit

**Scope**: Cross-feed reconciliation, source reliability scoring, and audit lineage  
**Module**: `mdrap.reconciliation`  
**Date**: 2026-10-09  

---

## 1. Multi-Feed Conflict Resolution Protocol

When multiple feeds publish observations for the same instrument within temporal threshold $\Delta t \le 0.25\text{ s}$:
1. **Price Divergence Check**: If $|P_A - P_B| / P_{avg} > 0.5\%$, a cross-feed conflict is flagged.
2. **Reliability Weighted Selection**: The engine evaluates EWMA scores based on packet loss, schema errors, and latency:
   $$Score = 0.40 \cdot A + 0.25 \cdot C + 0.20 \cdot D + 0.15 \cdot L$$
3. **Canonical Selection**: The feed with the higher score is published as canonical.
4. **Lineage Preservation**: The resulting `CanonicalDecision` documents both competing feeds, individual prices, and the selection rationale.

---

## 2. Invariant Compliance

- **No Cache Mutation**: Decisions are recorded on provenance objects without mutating cached prior events.
- **Audit Lineage**: Reconciled events link to their origin raw event ID, preserving complete regulatory auditability.
