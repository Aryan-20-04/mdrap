# MDRAP Pilot Readiness Audit — Phase 3: Cross-Agent Review & Reconciliation

**Lead Orchestrator:** Multi-Agent Engineering Lead  
**Audit Target:** MDRAP MVP Pilot Candidate (`product-focus/mdrap-reliability-platform`)  
**Commit:** `096d407`  
**Date:** 2026-11-11  

---

## 1. Multi-Agent Consensus & Dispute Reconciliation

| Domain | Agent 1–7 Findings | Agent 8 (Adversarial) Finding | Lead Orchestrator Reconciliation & Verdict |
| :--- | :--- | :--- | :--- |
| **Event Provenance** | Agent 1 observed in-memory ring buffer and identified incomplete fallback to `lineage`. | Agent 8 proved that post-restart, all payload SHA-256 and lineage hashes are lost. | **UNANIMOUS CONSENSUS (CRITICAL DEFECT)**. Provenance must be stored in a dedicated persistent `event_provenance` SQLite table. In-memory volatility violates core design invariants. |
| **Deterministic Replay** | Agent 2 verified bitwise-identical state digests on replaying `inc-1794334613-0001`. | Agent 8 proved that `cli.py replay --incident` crashes out-of-the-box due to DB path mismatch, and leaked SQLite handles cause Windows file locks. | **RECONCILED (MEDIUM DEFECT)**. The replay core is mathematically sound, but CLI argument defaults and SQLite handle lifecycle must be remediated before pilot deployment. |
| **Cryptography & Lineage** | Agent 3 verified RFC 6962 Merkle tree domain separation and inclusion proofs. | Agent 8 warned that MDRAP cannot prove external exchange authorship without venue digital signatures. | **RECONCILED (DOCUMENTATION REQUIREMENT)**. Cryptographic primitives are verified. Evidence boundary must be documented explicitly in pilot onboarding specs. |
| **Connector Safety** | Agent 4 verified zero order routing endpoints, 100% read-only ingestion. | Agent 8 noted that a universal 10s stale threshold is inflexible for low-frequency pairs. | **RECONCILED (LOW DEFECT)**. Read-only safety is absolute. Expose `stale_timeout_ms` as a configurable parameter. |
| **Chaos & Conformance** | Agent 5 verified 4/4 passing conformance scenarios in `ConnectorCertificationSuite`. | Agent 8 confirmed conformance pass, but challenged whether failures generate incidents. | **RECONCILED (ENHANCEMENT)**. Conformance engine is robust; ensure persistent incident recording. |
| **Test Infrastructure** | Agent 7 verified 1,749 clean tests, 0 collection errors, 33/33 MVP tests passing. | Agent 8 noted lack of an end-to-end restart verification test. | **RECONCILED (TEST ADDITION)**. Write comprehensive `tests/test_pilot_readiness.py` covering the full 12-step lifecycle. |

---

## 2. Master Remediation Scope (Pre-Remediation Gate)

The multi-agent council agrees on a focused, 4-item remediation plan:
1. **Remediation Item 1 (Durability):** Add persistent SQLite `event_provenance` table schema and update `ProvenanceTracker` (`record` and `get`) to ensure complete forensic evidence survives restarts.
2. **Remediation Item 2 (Handle Lifecycle):** Fix SQLite connection lifecycle in `src/mdrap/incidents.py` (`IncidentManager`) with explicit `try ... finally: conn.close()` to prevent Windows file locking.
3. **Remediation Item 3 (CLI Resolution):** Update `cmd_replay` in `src/mdrap/cli/operations.py` to intelligently fall back to `"data/mdrap.db"` when `--db` is not explicitly provided.
4. **Remediation Item 4 (Configurable Staleness):** Add configurable `stale_timeout_ms` parameter to `WebSocketFeedManager.__init__()` in `src/mdrap/ws_feed.py`.
5. **Remediation Item 5 (Verification Suite):** Implement `tests/test_pilot_readiness.py` to independently verify the complete 12-step lifecycle across process restarts.
