# MDRAP Phase 6 — Continuous Integration (CI) Quality Gates & Pipeline Specification

## 1. Executive Summary & Pipeline Contract
To prevent untested, degraded, or vulnerable code from entering the main codebase or production environments, MDRAP establishes a strict 5-stage **Continuous Integration Quality Gate Pipeline**.

Every pull request, commit, and release candidate must pass all gates sequentially. **Zero gate bypasses or overrides are permitted.**

---

## 2. Five-Stage CI Quality Gate Pipeline

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Gate 1:    │     │   Gate 2:    │     │   Gate 3:    │     │   Gate 4:    │     │   Gate 5:    │
│  Lint/Style  │ ──> │ Static Types │ ──> │ Unit / Tests │ ──> │ Benchmarks   │ ──> │ Security     │
│              │     │              │     │              │     │              │     │              │
│ • Ruff / PEP8│     │ • MyPy / ty  │     │ • 100% Pass  │     │ • ≥15,000 eps│     │ • pip-audit  │
│ • Zero warn  │     │ • Zero error │     │ • 1,212 test │     │ • p99 ≤ 50µs │     │ • 0 High/Crit│
└──────────────┘     └──────────────┘     └──────────────┘     └──────────────┘     └──────────────┘
```

---

## 3. Detailed Quality Gate Specifications

| Gate ID | Verification Stage | Tooling & Command | Pass Criteria (Blocking) | Failure Action |
| :--- | :--- | :--- | :--- | :--- |
| **GATE-1** | Code Style & Formatting | `ruff check .` | 0 errors, 0 warnings | PR blocked from review |
| **GATE-2** | Static Type Checking | `mypy src/ --strict` | 0 type errors on public APIs | PR blocked from merge |
| **GATE-3** | Unit & Scaling Regression | `python -m pytest tests/ -v` | **100% pass (1,212/1,212)** | PR blocked from merge |
| **GATE-4** | Performance & Regressions | `python benchmarks/phase6_scaling_benchmark.py` | $\ge 15,000\text{ eps}$, $p99 \le 50\text{ \mu s}$ | Performance regression alert; blocked |
| **GATE-5** | Security & Dependencies | `pip-audit`, Bandit scan | 0 High or Critical CVEs | Security review required |

---

## 4. Pipeline Execution Commands (Reproducible Runbook)

To execute the complete quality gate suite locally before pushing or releasing:

```bash
# Gate 1 & 2: Lint and Typecheck
python -m ruff check src/ tests/
python -m mypy src/ --ignore-missing-imports

# Gate 3: Automated Pytest Regression
python -m pytest tests/test_phase6_scaling.py tests/test_phase5_pilot.py -v
python -m pytest tests/ -q

# Gate 4: Empirical Scaling Benchmark
python benchmarks/phase6_scaling_benchmark.py

# Gate 5: Security & Supply Chain Audit
python -m pip_audit
```

---

## 5. Branch Protection & Enforcement Rules
1. **Protected Branch**: Direct commits to `main` are prohibited; all changes must merge via pull requests.
2. **Required Approvals**: Minimum of 2 senior platform engineers.
3. **Green CI Required**: All 5 quality gates must report `SUCCESS` before merge buttons are enabled.
4. **Strict Local Operations**: Code agents and automated workers are restricted to local git operations (`git commit`) and **never execute `git push`**.
