# MDRAP Phase 4 — Product Website, Developer Documentation, Live Demonstration & Customer Onboarding Baseline

**Date**: 2026-11-11  
**Lead Orchestrator**: Lead Engineering Architect & Gatekeeper  
**Milestone**: Phase 4 — Institutional Product Experience, Developer Docs, Interactive Demo & Customer Onboarding  
**Active Branch**: `product/website-and-onboarding` (forked from `main` @ `f455dba`)  
**Base Release Tag**: `v6.0.0`  
**Distribution Artifacts**: `dist/mdrap_core-6.0.0-py3-none-any.whl`, `dist/mdrap_core-6.0.0.tar.gz`  

---

## 1. System State & Inspection Baseline

| Dimension | Inspection Finding | Verification Status |
| :--- | :--- | :--- |
| **Git Working Tree** | Clean working tree on `product/website-and-onboarding` | Verified via `git status` |
| **Release Tag** | `v6.0.0` tagged on `f7734b2` / release manifest at `f455dba` | Verified in git history |
| **Packaging Baseline** | Wheel `dist/mdrap_core-6.0.0-py3-none-any.whl` (766 KB) | Verified SHA-256 and install |
| **Core Dependency Rule** | Zero mandatory dependencies (`dependencies = []`) | Verified in `pyproject.toml` |
| **Regression Suite** | 1,677 passed, 1 skipped, 40 deselected, 0 failed, 0 errors | Verified in `pytest_results.xml` |
| **Read-Only Safety** | AST scan confirmed 0 trade execution / order endpoints | Verified via `check_read_only_safety.py` |
| **Operational Demo** | 12-step demo script in `tools/run_customer_demo.py` | Verified (8.2s complete run) |
| **Frontend / Website** | Previously absent; directory `website/` initialized | Requires full institutional build |
| **Developer Docs** | Legacy mixed docs; `docs/developer/` initialized | Requires systematic rewrite |
| **Deployment Scripts** | Previously unbundled; `deploy/` initialized | Requires Docker, systemd, runbooks |

---

## 2. Interface Agreements & Work Breakdown

To ensure zero collision across the 9 agents during parallel execution, file boundaries and interface contracts are explicitly assigned:

```
+-----------------------------------------------------------------------------------------+
|                                    LEAD ORCHESTRATOR                                    |
|                   (Architecture Invariants, Quality Gates, Release Decisions)           |
+-------------+----------------------+--------------------+--------------------+----------+
              |                      |                    |                    |
       +------v------+        +------v------+      +------v------+      +------v------+
       |   AGENT 1   |        |   AGENT 2   |      |   AGENT 3   |      |   AGENT 4   |
       | Website & UI|        |Developer Doc|      |Interactive  |      |Self-Hosted  |
       |  `website/` |        |`docs/devel/`|      |    Demo     |      | Deployment  |
       |             |        |             |      |`tools/demo/`|      |  `deploy/`  |
       +------+------+        +------+------+      +------+------+      +------+------+
              |                      |                    |                    |
       +------v------+        +------v------+      +------v------+      +------v------+
       |   AGENT 5   |        |   AGENT 6   |      |   AGENT 7   |      |   AGENT 8   |
       | Native Dist |        | Technical   |      | DevEx & GH  |      | Customer    |
       | Engineering |        |  Marketing  |      | Community   |      | Onboarding  |
       |`audit/ph4/` |        |`bus/ph4/cnt/`|     |Root / .gh/  |      |`bus/ph4/onb/`|
       +------+------+        +------+------+      +------+------+      +------+------+
              |                      |                    |                    |
              +----------------------+--------------------+--------------------+
                                     |
                              +------v------+
                              |   AGENT 9   |
                              | Adversarial |
                              | Reviewer    |
                              +-------------+
```

### Interface Assignments:
1. **Agent 1 (Website)**: Owns `website/**` and `docs/website/**`. Standalone institutional static website (HTML5, responsive CSS, vanilla JS, accessible, dark technical aesthetic, working interactive demo simulator and contact/pilot request flow).
2. **Agent 2 (Developer Docs)**: Owns `docs/developer/**`. 16 clean, comprehensive markdown guides with tested commands and exact API/CLI specs.
3. **Agent 3 (Demo)**: Owns `tools/demo/**`. Two demonstration modes:
   - Mode A: Local deterministic synthetic scenario (reproducible, offline, zero network required).
   - Mode B: Live public feed monitor (Binance/Coinbase/Kraken public ticker/depth, clearly labeled LIVE, graceful fallback).
4. **Agent 4 (Deployment)**: Owns `deploy/**` and `docs/developer/self-hosted-deployment.md`. Systemd unit file, Dockerfile, docker-compose.yml, environment validation script, backup/restore script, healthcheck script.
5. **Agent 5 (Native Distribution)**: Owns `audit/phase4/NATIVE_DISTRIBUTION.md`. In-depth technical analysis of wheel classification, ctypes loading, pure Python parity, and compilation mechanisms.
6. **Agent 6 (Technical Marketing)**: Owns `business/phase4/content/**`. Real technical marketing content, blog posts, architecture explainers, why sequence gaps matter, deterministic replay value.
7. **Agent 7 (DevEx & Community)**: Owns root `README.md`, `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `CHANGELOG.md`, and `.github/**`.
8. **Agent 8 (Customer Onboarding)**: Owns `business/phase4/onboarding/**`. 5-stage onboarding process, discovery questionnaire, pilot agreement template, exit procedures.
9. **Agent 9 (Adversarial Auditor)**: Owns `audit/phase4/ADVERSARIAL_REVIEW.md`, `WEBSITE_VERIFICATION.md`, `DEMO_VERIFICATION.md`, and `FINAL_PHASE4_REPORT.md`.

---

## 3. Mandatory Engineering Rules for Phase 4

- **No False Claims**: Zero invented customers, logos, testimonials, or certifications.
- **Accurate Benchmarks**: All performance numbers on the website and docs must cite empirical measurements from `AGENT_4_PERFORMANCE.md` and `audit/release-final/`.
- **Zero Execution Perimeter**: The website and docs must firmly state that MDRAP is a read-only passive sidecar with zero order-routing capabilities.
- **Transparent Simulation**: Synthetic data in the demo must be boldly labeled `SYNTHETIC / SIMULATED`. Live feeds must be labeled `LIVE PUBLIC FEED`.

Phase A baseline audit is **COMPLETE**. Parallel execution authorized.
