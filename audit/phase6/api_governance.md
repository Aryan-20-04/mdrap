# MDRAP Phase 6 — Public API Governance & Interface Deprecation Policy

## 1. Executive Summary & Policy Scope
As MDRAP matures from a single engine to a partitioned platform supporting multiple programming languages (C++, Rust, Java, Python), breaking changes to public APIs can halt downstream trading desks. This document establishes the governance policy for public APIs, SBE message templates, and deprecation schedules.

---

## 2. API Stability Tiers

| Stability Tier | Scope | Breaking Change Policy | Deprecation Requirement |
| :--- | :--- | :--- | :--- |
| **Tier 1: Wire Protocols** | SBE binary schemas, TCP stream frames | **Zero Breaking Changes** without major SemVer bump | 12-month advance notice |
| **Tier 2: Client SDKs** | C++, Rust, Java, Python SDK APIs | Strictly backward-compatible across minor versions | 6-month notice + runtime warning |
| **Tier 3: REST / Metrics** | `/health`, `/metrics`, `/api/v1` | Backward-compatible schema additions permitted | 3-month notice |
| **Tier 4: Internal Classes**| Engine private functions (`_worker_loop`) | Free to refactor without notice | None |

---

## 3. Deprecation and Migration Lifecycle

When an API method or module is scheduled for retirement (e.g., migrating from legacy `Pipeline` to `IngestLog` + `Engine`):

```
[ Active API ] ──> [ Deprecated with Runtime Warning ] ──> [ Formal EOL Release ] ──> [ Removal ]
                     (Minimum 6 Months Active)
```

1. **Warning Phase**: The deprecated function emits Python's standard `DeprecationWarning` naming the replacement API and version of removal.
2. **Dual-Support Phase**: Both the legacy API and new API function concurrently with identical semantic guarantees.
3. **Removal Phase**: Removal occurs strictly upon a major SemVer release (`v2.0.0`).
