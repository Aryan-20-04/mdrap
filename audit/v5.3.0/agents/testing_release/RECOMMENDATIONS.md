# Testing, Build & Release Engineering — Recommendations

## Priority 0 (Release Blockers)
1. **Remove Bare Except in `test_fastpath.py` (F-TST-01)**:
   - Ensure per-tick status equality assertions fail loudly.
2. **Implement Genuine Assertion in `gen_claims.py` (F-TST-02)**:
   - Assert `claim_str in current_claim`, exiting with code 1 upon mismatch.
3. **Remove `-march=native` from Wheel Build Flags (F-TST-03)**:
   - Use `-O3` with a portable baseline target (`-march=x86-64-v3` or baseline `x86-64`).
4. **Synchronize Package Version to 5.3.0 (F-TST-04)**.

## Priority 1 (CI/CD & Isolation)
5. **Publish SDist in `wheels.yml` (F-TST-05)**:
   - Add `python -m build --sdist` job.
6. **Run Full Test Suite Under Pure Python Fallback in CI (F-TST-07)**.
7. **Harden Shared Memory Test Lifecycle (F-TST-06)**:
   - Wrap SHM allocations in fixtures with deterministic unlink.\n