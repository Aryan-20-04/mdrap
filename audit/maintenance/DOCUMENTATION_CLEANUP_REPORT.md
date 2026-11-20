# MDRAP 6.0.8 — Documentation Cleanup, Deduplication, and Alignment Report

**Date:** 2026-11-20  
**Phase:** Phase 2 — Documentation Cleanup and Update  
**Platform Version:** MDRAP v6.0.8 (Read-Only Infrastructure Release)  
**Governance Invariant:** STRICT NO DEPLOYMENT (Zero Live Execution / Strategy Activation)  

---

## 1. Executive Summary

Following the core architecture pruning in release 6.0.8 (removal of 28 non-infrastructure modules and 4 heavy optional dependencies), the documentation suite was audited, consolidated, and purged of duplicate, stale, and out-of-scope files. All references to trading strategies, execution gates, order management, and deprecated components were eliminated or updated to accurately represent MDRAP as a pure read-only market data infrastructure sidecar.

---

## 2. Removed Obsolete and Duplicate Files (24 Files Total)

The following 24 redundant or out-of-scope markdown files were removed via `git rm`:

### A. Execution & Trading Guides (Out-of-Scope Under NO DEPLOYMENT)
1. `docs/RESEARCH_AND_TRADING.md` — Contained obsolete strategy execution instructions and paper trading workflows.
2. `docs/SHADOW_MODE_EVALUATION.md` — Contained deprecated shadow-mode order matching evaluation steps.

### B. Obsolete Duplicate Directory Indices & Starter Guides
3. `docs/README.md` — Redundant duplicate index file duplicating root `README.md`.
4. `docs/starters/data-vendor.md` — Redundant starter tutorial.
5. `docs/starters/hft-desk.md` — Out-of-scope algorithmic trading guide.
6. `docs/starters/research-platform.md` — Redundant starter tutorial superseded by `docs/quickstart.md` and `docs/guides/`.

### C. Redundant Duplicate Developer Directory (`docs/developer/` — 18 Files)
All 18 files in `docs/developer/` were exact or stale duplicates of the primary canonical documentation in `docs/` and root:
7. `docs/developer/api-reference.md` (superseded by `docs/reference/api.md` & `docs/API_STABILITY.md`)
8. `docs/developer/architecture.md` (superseded by `docs/architecture.md`)
9. `docs/developer/cli-reference.md` (superseded by `docs/cli-reference.md`)
10. `docs/developer/configuration.md` (superseded by `docs/configuration.md` & `docs/reference/config.md`)
11. `docs/developer/connectors.md` (superseded by `docs/extending/feed-adapter.md`)
12. `docs/developer/demo.md` (superseded by `docs/quickstart.md`)
13. `docs/developer/deployment.md` (superseded by `docs/deployment.md`)
14. `docs/developer/evidence-verification.md` (superseded by `docs/spec/benchmark-methodology-v2.md`)
15. `docs/developer/getting-started.md` (superseded by `docs/quickstart.md`)
16. `docs/developer/incidents.md` (superseded by `docs/troubleshooting.md`)
17. `docs/developer/installation.md` (superseded by `docs/installation.md`)
18. `docs/developer/limitations.md` (superseded by `docs/product/scope-and-guarantees.md`)
19. `docs/developer/provenance.md` (superseded by `docs/product/EVENT_PROVENANCE.md`)
20. `docs/developer/quality-engine.md` (superseded by `docs/quality-rules.md` & `docs/reference/rules.md`)
21. `docs/developer/replay.md` (superseded by `docs/product/DETERMINISTIC_REPLAY.md`)
22. `docs/developer/security.md` (superseded by `docs/security.md`)
23. `docs/developer/self-hosted-deployment.md` (superseded by `docs/deployment.md`)
24. `docs/developer/troubleshooting.md` (superseded by `docs/troubleshooting.md`)

---

## 3. Retained & Updated Documentation

The following core documentation files were audited and synchronized with MDRAP 6.0.8 architecture:

| File | Key Changes Applied |
| :--- | :--- |
| `README.md` | Aligned version badge to `6.0.8`, updated passing test badge to `1,400+ passing`, updated architecture diagram to show 5 venue feeds and dual delivery (`IngestLog WAL` & `SHM Ring Buffer` -> `SQLite WAL`), removed claims of matching engine/14 risk gates, verified feed registration metadata-only notice. |
| `CHANGELOG.md` | Added comprehensive `[6.0.8] - 2026-11-20` entry documenting removal of 28 pruned modules (-16,693 LOC), decoupling of 4 heavy optional dependencies, 2.1x throughput gains, and sub-microsecond IPC latency. |
| `docs/map.md` | Replaced legacy architecture map with exact 93-module inventory categorized across Core Engine, Ingestion, Quality, Storage, IPC/Service, and Native Acceleration tiers. Updated Mermaid diagram. |
| `docs/API_STABILITY.md` | Replaced `kafka_sink` with `service.py` (`MarketDataDaemon`, `StreamClient`), clarified decoupled strategy companion packages, confirmed stable contracts for `SHMReader` and `StreamClient`. |
| `docs/cli-reference.md` | Purged 12 deleted CLI commands (`alert`, `backtest`, `bars`, `corpact`, `features`, `markets`, `news`, `options`, `portfolio`, `risk`, `schedule`, `watchlist`). Verified 57 active commands against CLI parser. |
| `docs/architecture.md` | Cleaned Section 2.7 (removed `navigator.py`) and Section 2.8 (removed `kafka_sink.py`). Highlighted pure Python/C dual-tier architecture and zero-lock seqlock ring buffers. |
| `docs/editions.md` | Removed stale references to internal core shims; marked commercial extensions as fully decoupled companion packages. |
| `docs/SDK_GUIDE.md` | Completely rewritten to focus on the read-only infrastructure SDK: `StreamClient` for TCP streaming, `SHMReader` for shared memory IPC, non-blocking queue ingestion, and historical querying via `Engine.open()`. |
| `CONTRIBUTING.md` | Fixed internal links from legacy `src/*.py` paths to canonical `src/mdrap/*.py` paths. |
| `docs/decisions/0005-backbone-stability-policy.md` | Updated pruned module references to clean code annotations; corrected relative link to `CONTRIBUTING.md`. |
| `docs/extending/*.md` | Fixed relative file links in `alert-sink.md`, `auth-provider.md`, `feed-adapter.md`, `output-sink.md`, `quality-rules.md`, `storage-backend.md`, and `README.md`. |
| `docs/fpga-spike-findings.md` | Converted historical research spike file references to clean code spans. |
| `docs/v5/research/DPDK_AND_INGRESS_ANALYSIS.md` | Converted host absolute URI references to clean code spans. |

---

## 4. Broken Link Audit

An automated scan of all relative markdown links across `docs/` and root markdown files was performed:

- **Initial Broken Links:** 23
- **Resolutions Applied:**
  - 6 links in `CONTRIBUTING.md` updated to `src/mdrap/`.
  - 10 links in `docs/decisions/0005-backbone-stability-policy.md` converted to code annotations or valid relative targets.
  - 5 links in `docs/fpga-spike-findings.md` converted to code spans.
  - 2 links in `docs/v5/research/DPDK_AND_INGRESS_ANALYSIS.md` converted to code spans.
- **Final Broken Links Remaining:** **0**

---

## 5. Automated Verification Results

All automated documentation and claims verification test suites pass cleanly:

```
tests/test_docs_verification.py::test_readme_relative_links_exist PASSED
tests/test_docs_verification.py::test_public_sdk_imports_match_documentation PASSED
tests/test_docs_verification.py::test_feed_adapter_documented_imports PASSED
tests/test_docs_verification.py::test_user_documentation_does_not_claim_unserved_ports_or_publish_fake_keys PASSED
tests/test_docs_verification.py::test_ci_and_test_configuration_do_not_force_demo_mode PASSED
tests/test_docs_verification.py::test_cli_version_matches_package_version_source PASSED
tests/test_docs_verification.py::test_feed_registration_docs_describe_metadata_only_runtime PASSED
tests/test_docs_verification.py::test_quickstart_canonical_engine_walkthrough PASSED
tests/test_claims_verification.py::test_c04_version_consistency PASSED
tests/test_claims_verification.py::test_c01_readme_and_manifest_alignment PASSED
tests/test_claims_verification.py::test_c02_forbidden_claims_scan PASSED
tests/test_claims_verification.py::test_c05_single_node_consensus_disclosure PASSED
tests/test_claims_verification.py::test_x01_call_site_review_record PASSED
tests/test_claims_scanner.py::test_claims_manifest_is_valid PASSED
tests/test_claims_scanner.py::test_documentation_free_of_forbidden_superlatives PASSED
tests/test_claims_scanner.py::test_scanner_detects_deliberate_violation PASSED
tests/test_claims_scanner.py::test_claims_scanner_cli_invocable PASSED

======================== 17 passed, 1 warning in 4.93s ========================
```
