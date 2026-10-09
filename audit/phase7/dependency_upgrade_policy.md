# MDRAP Phase 7 — Dependency Upgrade Policy & Security Response Procedure

## 1. Executive Summary & Policy Scope
Uncontrolled or automatic dependency upgrades introduce breaking changes, supply chain vulnerabilities, and performance regressions.

This document establishes the mandatory **8-Step Dependency Upgrade Protocol** required before updating any external package in MDRAP.

---

## 2. Mandatory 8-Step Dependency Upgrade Protocol

```
1. Baseline Verification   ──>  2. Release Note Audit  ──>  3. Controlled Branch
   Run full test suite          Inspect CVEs & changelog       Create isolated PR branch
                                                                      │
                                                                      ▼
6. Rebuild Native Kernels  <──  5. Regression Matrix   <──  4. Focused Package Tests
   Compile C extensions         Run all 1,267 tests            Verify direct package calls
         │
         ▼
7. Performance Comparison  ──>  8. Release Documentation & Approval
   Run scaling benchmark        Log upgrade in dependency inventory
```

---

## 3. Vulnerability Response SLA
When a CVE is published against an active dependency:
- **Critical (CVSS 9.0–10.0)**: Remediated, tested, and released within **24 hours**.
- **High (CVSS 7.0–8.9)**: Remediated and released within **7 calendar days**.
- **Medium / Low**: Evaluated and batched into the next scheduled monthly release.

### Real Code Path Verification
In compliance with Rule 9 ("Do not claim a vulnerability is fixed solely because a scanner no longer reports it"), every CVE advisory is audited against the actual code path in `src/` to determine whether MDRAP imports or executes the vulnerable function.
