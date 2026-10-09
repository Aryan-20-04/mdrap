# MDRAP Phase 4 — Known Risks Register

**Document Identifier**: `MDRAP-RISKS-P4-001`  
**Date**: October 9, 2026  

---

## 1. Pre-Validation Known Risks

| Risk ID | Component | Description | Impact | Initial Mitigation |
|---|---|---|---|---|
| **RISK-P4-01** | Toolchains | Rust compilation not executable on current Windows host (`cargo` missing). | Low (Non-blocking) | Document limitation; deliver valid crate code; validate C++ & Java SDKs. |
| **RISK-P4-02** | Saturation | Saturated SQLite WAL writes under sustained tick load (>100k EPS). | Medium | Batching via group commit and IngestLog WAL buffer decoupling. |
| **RISK-P4-03** | Network Partition | Simultaneous split-brain assertion during cluster network split. | Critical | Monotonic epoch fencing tokens (`StaleEpochError`) and node_id tie-breaker. |
| **RISK-P4-04** | Deprecations | Legacy code still importing `Pipeline` or `BinaryJournal`. | Low | Deprecation shims with automated warnings pointing to `IngestLog` and `Runtime`. |
