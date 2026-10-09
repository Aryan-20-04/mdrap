# Phase 4 CI Release Gates & Quality Verification

**Pipeline Specification**: Automated Release Gates G0 through G8  
**Timestamp**: 2026-10-09  

---

## 1. Automated Pipeline Gates

Every pull request and release tag must pass the full sequential gate matrix:

1. **Gate 0 (Preflight & Hygiene)**:
   - Git working-tree hygiene.
   - License audit & dependency vulnerability scan (`pip-audit` / safety).
2. **Gate 1 (Unit & Functional Verification)**:
   - Full test execution: `pytest tests/ -v`.
   - Invariant: Zero regressions across Phase 0, 1, 2, 3 suites.
3. **Gate 2 (Dual Execution Semantic Parity)**:
   - Test execution under native C acceleration.
   - Test execution under pure Python fallback (`MDRAP_DISABLE_FASTPATH="1"`).
   - Invariant: 100% output identity between C and Python kernels.
4. **Gate 3 (Performance Gate)**:
   - Benchmark throughput must not regress by >5% against historical baselines.
   - Microsecond p99 latency must stay <= 500 µs.
5. **Gate 4 (Fault Injection & Recovery Gate)**:
   - Truncated WAL recovery, simulated socket drops, split-brain fencing assertions.
6. **Gate 5 (Security Gate)**:
   - Salted token authentication, CIDR allowlisting, fail-closed licensing checks.
7. **Gate 6 (Release Sign-off)**:
   - Hash manifest generation, documentation freshness review, release note assembly.
