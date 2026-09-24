# Contributing to MDRAP

Guidelines and engineering standards for contributing to the **Market Data Reliability & Acceleration Platform (MDRAP)**.

---

## 0. Core Engineering Rule: The "No Skipping Gates" State Machine

Every contribution, whether submitted by core maintainers or external contributors, must progress through this strict state machine. **No skipping gates.**

```
                    ┌──────────────────┐
                    │   DEVELOPMENT    │
                    └────────┬─────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │ LOCAL UNIT TESTS │
                    └────────┬─────────┘
                             │ PASS
                             ▼
                    ┌──────────────────┐
                    │ STATIC ANALYSIS  │
                    │ SECURITY CHECKS  │
                    └────────┬─────────┘
                             │ PASS
                             ▼
                    ┌──────────────────┐
                    │ INTEGRATION TEST │
                    └────────┬─────────┘
                             │ PASS
                             ▼
                    ┌──────────────────┐
                    │ MANUAL INSPECTION│
                    └────────┬─────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │ LOCAL CHECKPOINT │
                    └────────┬─────────┘
                             │
                             ▼
                 ┌──────────────────────────┐
                 │ MERGE INTO LOCAL RELEASE │
                 │ CANDIDATE                │
                 └────────────┬─────────────┘
                              │
                              ▼
                 ┌──────────────────────────┐
                 │ COMPLETE SYSTEM TEST     │
                 └────────────┬─────────────┘
                              │
                              ▼
                 ┌──────────────────────────┐
                 │ PERFORMANCE BENCHMARKS   │
                 └────────────┬─────────────┘
                              │
                              ▼
                 ┌──────────────────────────┐
                 │ DOCUMENTATION UPDATE     │
                 └────────────┬─────────────┘
                              │
                              ▼
                 ┌──────────────────────────┐
                 │    RELEASE CANDIDATE     │
                 └────────────┬─────────────┘
                              │
                              ▼
                 ┌──────────────────────────┐
                 │ FINAL RELEASE VALIDATION │
                 └────────────┬─────────────┘
                              │
                              ▼
                       ┌─────────────┐
                       │    PUSH     │
                       │   RELEASE   │
                       └─────────────┘
```

---

## The MDRAP Development Protocol

1. **Never modify `main` directly.** All work occurs on feature or fix branches branching from `develop`.
2. **Every feature begins in its own branch.** Branch naming convention: `feature/<topic>`, `fix/<issue>`, or `release/<version>`.
3. **Every feature must have tests.** Untested code is dead code.
4. **Existing tests must continue to pass.** Zero test regressions allowed across the 800+ suite.
5. **New functionality must not silently change existing public APIs.** Adhere to [`docs/API_STABILITY.md`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/docs/API_STABILITY.md).
6. **Security-sensitive changes require explicit security testing.**
7. **Performance-sensitive changes require benchmarks.** Measure before claiming; record JSON outputs under `benchmarks/`.
8. **Native code requires sanitizer testing.** C fastpath hot paths require memory safety verification.
9. **Changes are locally validated before integration.** Run `./scripts/check.sh` locally.
10. **A validated local checkpoint is created before merging the feature.**
11. **The complete system is tested after integration.**
12. **Performance is benchmarked after integration.**
13. **Documentation is updated only after the implementation has passed validation.** Docs reflect tested reality, not intentions.
14. **Documentation examples must be executable or verified.**
15. **Release candidates are frozen.** No new features enter a release candidate.
16. **Release candidates are rebuilt from a clean environment.**
17. **Only fully validated release candidates are tagged and pushed.**
18. **Every release receives a changelog.** Group into *Added*, *Changed*, *Fixed*, *Security*, *Performance*, and *Breaking Changes*.
19. **Breaking changes require a major version.**
20. **No feature is considered stable until it has sufficient tests, documentation, and real-world validation.**

---

## Branch Architecture

```
main (Production releases only, tagged vX.Y.Z)
 │
 ├── develop (Active integration branch)
 │    │
 │    ├── feature/quality-v2
 │    ├── feature/websocket-hardening
 │    ├── feature/feed-sdk
 │    │
 │    └── fix/issue-123
 │
 └── release/v1.0.0-rc1
```

- **`main`**: Protected. Only accepts merges from fully validated release candidates (`release/vX.Y.Z`).
- **`develop`**: Primary collaboration branch. All features and bug fixes merge here via PR after passing all 9 local gates.
- **`feature/*`**: Isolated feature branches.
- **`fix/*`**: Bug fixes and security patches.
- **`release/*`**: Release candidate branches created for stabilization, final benchmarking, and clean rebuild validation.

---

## Setup & Prerequisites

- Python 3.10+
- GCC or Clang (for native C fastpath compilation)

```bash
git clone https://github.com/Aryan-20-04/mdrap.git
cd mdrap
git checkout develop   # Always branch from develop!
pip install -e ".[all]"
python build_fastpath.py
```

> [!TIP]
> - **Full Development Environment (Recommended)**: `pip install -e ".[all]"` (or `pip install -r requirements.txt`) installs all optional modules, API frameworks (`fastapi`, `uvicorn`, `httpx`), and testing tools (`pytest`, `pytest-asyncio`, `pytest-cov`, `pytest-timeout`).
> - **Zero-Dependency Minimal Core Engine**: `pip install -e .` installs the engine using pure Python standard library only.

---

## The Local Validation Command (`check.sh`)

Before opening any pull request or committing changes, execute the 9-gate validation script:

```bash
./scripts/check.sh        # Linux / macOS / Git Bash
# or:
.\scripts\check.bat       # Windows Command Prompt
python scripts/check.py   # Universal cross-platform runner
```

This single command executes:
1. **Code formatting check**: `ruff format --check src/`
2. **Static analysis & linting**: `ruff check src/`
3. **Rules single-source-of-truth parity**: `python tools/gen_reasons.py --check`
4. **Unit test suite**: Fast path core unit tests
5. **Integration test suite**: Guarantees, decoupled sinks, and end-to-end pipelines
6. **Security checks**: RBAC boundaries, token sanitization, and environment diagnostics
7. **Dependency checks**: CVE hygiene thresholds
8. **Native compilation**: `python build_fastpath.py`
9. **Native tests**: Fastpath library load & FFI micro-benchmark smoke check

If any check fails, the script exits immediately with code `1`. **Nothing proceeds.**

---

## Public API & Extension Development

Consult [`docs/API_STABILITY.md`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/docs/API_STABILITY.md) for stability contracts (`STABLE`, `BETA`, `EXPERIMENTAL`, `INTERNAL`).

MDRAP provides standardized extension interfaces for integrating external venues, custom validation logic, and alternate persistence engines:

### 1. Adding a New Feed Adapter
Exchange and venue integrations implement the [`FeedAdapter`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/protocols.py) protocol (`open()`, `__iter__()`, `close()`) and emit standardized [`RawEvent`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/models.py) objects.
- Guide: [Feed Adapter Development](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/docs/extending/feed-adapter.md)

### 2. Adding a Custom Quality Rule
User-defined validation rules are registered in the user bitmask range (bits 32–63) using the [`@register_rule`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/rules.py) decorator from [`src/rules.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/rules.py). User rules run post-native evaluation and cannot crash the pipeline or downgrade existing anomaly statuses.
- Guide: [Custom Quality Rules](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/docs/extending/quality-rules.md)

### 3. Implementing a Storage Backend
Custom persistence engines implement the [`StorageBackend`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/protocols.py) protocol defined in [`src/protocols.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/protocols.py).
- Guide: [Storage Backend Implementation](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/docs/extending/storage-backend.md)

---

## Baseline Verification Report

For the frozen system baseline data, host specifications, and full module catalog, see [`docs/development/baseline.md`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/docs/development/baseline.md).
