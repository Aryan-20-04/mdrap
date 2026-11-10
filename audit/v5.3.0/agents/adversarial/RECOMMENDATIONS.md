# Independent Adversarial Reviewer — Recommendations

## Priority 0 (Release Blockers)
1. **Unify Canonical Version to 5.3.0 (F-ADV-01)**.
2. **Purge Committed Binaries from Git (F-ADV-03)**:
   - Remove `_fastpath_native.dll` and `mdrap-core.exe` from Git tracking and add to `.gitignore`.
3. **Synchronize README Benchmarks with Raw Evidence (F-ADV-02)**:
   - Correct Native C throughput to 416.3k EPS, Python to 149.9k EPS, and speedup to 2.78x.
   - Correct reproduction command for isolated C kernel.

## Priority 1 (Accurate Documentation)
4. **Retitle Soak Test Report (F-ADV-04)**:
   - Retitle `24H_SOAK_TEST_REPORT.md` to `BURST_SOAK_AND_STABILITY_REPORT.md` and explicitly label 24-hour figures as mathematical projections.
5. **Clarify Edition Boundaries in README (F-ADV-07)**:
   - Clearly delineate core features vs features requiring `packages/mdrap-strategies`.
6. **Label Blueprint Modules Explicitly (F-ADV-05)**:
   - Document `af_xdp.py` and `multicast_arbitrator.py` as simulation harnesses.

## Priority 2 (Code Hygiene)
7. **Delete Dead Modules (F-ADV-08)**:
   - Remove `trading_cli.py`, `shm_drainer.py`, `scheduler.py`, `journal.py`.
8. **Remove Hardcoded Demo Token (F-ADV-09)**.\n