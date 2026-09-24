# MDRAP Release Lifecycle & Next-Cycle Protocol (Phases 28–30)

## 1. Release Progression State Machine

Every release version in MDRAP must follow this strict unidirectional lifecycle:

```
┌────────────────────────────┐
│  LOCAL RELEASE CANDIDATE   │  (Phase 24: Functionality frozen, only fixes/docs)
└─────────────┬──────────────┘
              │
              ▼
┌────────────────────────────┐
│  FULL VALIDATION MATRIX    │  (Phase 25: All 9 quality gates passing via scripts/check.py)
└─────────────┬──────────────┘
              │
              ▼
┌────────────────────────────┐
│  CLEAN-ROOM REBUILD        │  (Phase 26: Fresh clone build verified via scripts/bootstrap.py)
└─────────────┬──────────────┘
              │
              ▼
┌────────────────────────────┐
│  ARTIFACT VERIFICATION     │  (Phase 27: SDist, Wheel, SHA256SUMS, SBOM manifest verified)
└─────────────┬──────────────┘
              │
              ▼
┌────────────────────────────┐
│  VERSION TAGGING (LOCAL)   │  (Phase 28: Annotated git tag created)
└─────────────┬──────────────┘
              │
              ▼
┌────────────────────────────┐
│  REMOTE RELEASE PUSH       │  (Only after user authorization)
└─────────────┬──────────────┘
              │
              ▼
┌────────────────────────────┐
│  POST-RELEASE MONITORING   │  (Phase 29: Clone verification, telemetry, triage)
└─────────────┬──────────────┘
              │
              ▼
┌────────────────────────────┐
│  NEXT DEVELOPMENT CYCLE    │  (Phase 30: Feature branching, repeating loop)
└────────────────────────────┘
```

---

## 2. Phase 28: Push Release Protocol

### Rules:
1. **Never tag or push without all 9 quality gates passing.**
2. **Never push to `main` directly.** Merges must flow from validated release candidate branches or `develop`.
3. **Never silently modify a tagged release afterward.** If a defect or patch is required, release `v2.3.1` (or next patch version).

### Local Tagging Commands:
```bash
# Verify working tree is clean and all gates pass:
python scripts/check.py

# Create annotated release tag:
git tag -a v2.3.0 -m "Release v2.3.0: Institutional Development Protocol, Canonical Model, Quality Hardening, SDK, and Benchmark Regression Gates"

# Push release branch and tags to remote (only when authorized):
git push origin develop --tags
```

---

## 3. Phase 29: Post-Release Verification Protocol

Immediately following a remote release or publish event, perform clean-room post-release validation:

### Checklist:
1. **Clean Clone Test**:
   ```bash
   git clone https://github.com/Aryan-20-04/mdrap.git mdrap-verify
   cd mdrap-verify
   python scripts/bootstrap.py
   ```
2. **Wheel Installation Test**:
   ```bash
   pip install dist/mdrap-2.3.0-py3-none-any.whl
   python -c "import mdrap; print('Installed mdrap:', mdrap.__version__)"
   ```
3. **Health & Smoke Verification**:
   ```bash
   python cli.py test
   python cli.py doctor
   ```
4. **Monitoring & Incident Response**:
   - Triage any GitHub issues or bug reports using rule `MD001`–`MD015` diagnostics.
   - Security disclosures reported via `SECURITY.md` protocol receive immediate CVE classification.

---

## 4. Phase 30: Next Development Cycle Protocol

Once a release cycle completes, the next development iteration commences through the standard branch-and-gate state machine:

```
  v2.3.0 Published
         │
         ▼
  Community Feedback & Issue Triage
         │
         ▼
  Milestone Prioritization (v2.4.0 / v3.0.0)
         │
         ▼
  Isolated Feature Branch (`feature/<name>`)
         │
         ▼
  Local Unit & Adversarial Tests
         │
         ▼
  Static Analysis & Security Doctor (`scripts/check.py`)
         │
         ▼
  Performance Benchmarking & Efficiency Sweep
         │
         ▼
  Documentation Sync (CHANGELOG.md, README.md)
         │
         ▼
  Release Candidate (RC Freeze)
```

No phase may be bypassed, and every optimization must demonstrate measurable correctness and latency proof before integration.
