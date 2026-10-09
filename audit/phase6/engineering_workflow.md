# MDRAP Phase 6 — Engineering Workflow & Sustainable Maintenance

## 1. Executive Summary & Developer Ergonomics
A platform cannot remain reliable if its development lifecycle is cumbersome, brittle, or requires hours to run integration tests. Following the `/ponytail` discipline, MDRAP enforces an ultra-fast, reproducible local engineering workflow relying strictly on the Python standard library and native pytest tooling.

---

## 2. Local Development & Verification Commands

```bash
# 1. Run core unit tests in < 2 seconds
python -m pytest tests/test_phase6_scaling.py tests/test_phase5_pilot.py -v

# 2. Run pure Python fallback parity check
$env:MDRAP_DISABLE_FASTPATH="1"; python -m pytest tests/test_phase4_packaging_and_delivery.py -v

# 3. Execute horizontal scaling benchmark
python benchmarks/phase6_scaling_benchmark.py

# 4. Generate sanitized diagnostic snapshot
python scripts/diagnostic_bundle.py --out tmp/diagnostic.json

# 5. Run deployment preflight check
python scripts/deploy_pilot.py --check-only
```

---

## 3. Sustainable CI/CD Quality Gates

1. **Fast-Feedback Tier (< 15 seconds)**: Code formatting (Ruff), type validation, and targeted unit test suites execute on every local git commit hook.
2. **Regression Tier (< 4 minutes)**: Full 1,200+ test repository suite runs before merging to `main`.
3. **Zero Deprecation Blindness**: Warnings emitted by deprecated modules are monitored to ensure consumers transition smoothly ahead of major releases.
