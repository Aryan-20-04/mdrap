---
name: mdrap-quality
description: >-
  Inspect, test, or extend MDRAP data quality rules, fault injection, and ground-truth validation.
  Use when adding new quality checks, modifying reconciliation logic, tuning anomaly thresholds,
  or writing tests for data quality detection.
---

# MDRAP Data Quality & Reconciliation Skill

This skill guides agents through maintaining, modifying, and testing data quality checks and cross-feed reconciliation logic.

## 1. Core Quality Principles

1. **Never Downgrade Status**: Priority map: `VALID (0) < SUSPICIOUS (1) < INVALID (2)`. An event flagged as `INVALID` (e.g. crossed quote or duplicate) must **never** be downgraded to `SUSPICIOUS` by a subsequent check.
2. **Never Silently Discard**: Invalid events are quarantined with full payloads into the `quarantine` table, never dropped.
3. **Numerically Stable Statistics**: Always use Welford's algorithm for rolling mean and variance. Test incoming points against the prior baseline before folding them into the window.

---

## 2. Adding a New Quality Rule

To add a new quality rule:
1. Define a new `Reason` enum in `src/models.py`:
   ```python
   class Reason(str, Enum):
       ...
       NEW_CHECK_FAILURE = "NEW_CHECK_FAILURE"
   ```
2. Implement the evaluation logic in `src/quality.py` inside `QualityEngine.evaluate()`:
   ```python
   if condition_fails:
       self._mark(event, QualityStatus.SUSPICIOUS, Reason.NEW_CHECK_FAILURE)
       self._bump(Reason.NEW_CHECK_FAILURE)
   ```
3. Add a unit test in `tests/test_quality.py`.
4. Run `python -m pytest tests/test_quality.py -v` to ensure zero regressions.

---

## 3. Tuning Reconciliation Weights

Dynamic source reliability scores in `src/reconciliation.py` are computed using weighted factors:
- `accuracy` (40%)
- `completeness` (25%)
- `dedup` (20%)
- `latency` (15%)

When tuning weights:
- Always run a baseline benchmark first (`cli.py benchmark --events 500000 --label pre_tune`).
- Apply the weight change in `Reconciler.reliability_score()`.
- Re-run the benchmark with the same seed (`cli.py benchmark --events 500000 --label post_tune`).
- Compare ground-truth reconciliation accuracy in the resulting JSON files.
