# MDRAP 6.0.0 Repository Health Report

**Audit date:** 2026-11-13  
**Revision inspected:** `b6e3003` (`remediation/v6.0.0-stage2-hardening`)  
**Scope:** source, C extension, packaging, tests, checked-in audit/benchmark evidence, focused runtime profiling. Production code was not changed.

## Executive summary

The reported Stage 2 changes address several real Stage 1 defects: archive extraction now uses `commonpath`, DLQ overflow is counted, channel identity is incorporated into WebSocket sequence keys, and built wheel cleanup excludes native binaries. They do not justify the “unconditionally approved” claims in the checked-in audit. A separate native engine lifetime remains unsafe: per-instance C engine pointers used by the normal `FastQualityEngine` path can be freed concurrently with evaluation. This is a confirmed P1 memory-safety defect.

The main measured Python costs are pipeline dispatch/reconciliation and serialization. In a fresh cProfile run of 3,000 deterministic simulated events, `process_one` consumed 0.578 s cumulative, `_dispatch_evaluated` 0.413 s, reconciliation 0.160 s, and JSON encoding accounted for 0.088 s across 11,309 `json.dumps` calls. These are profile-attributed costs, not a production latency claim. The checked-in legacy SQLite reader benchmark also shows sharply worse throughput beyond two threads.

No confirmed remotely exploitable input vulnerability was established in this inspection. There is, however, a native use-after-free risk under concurrent lifecycle calls. Packaging source looks corrected, but the wheel artifact test could not read `dist/` under this environment’s file permissions.

## Critical bugs and vulnerabilities

### P1 — Per-instance native engine can be freed during evaluation

**Evidence:** [fastpath.py] `src/mdrap/fastpath.py:1058-1073,1126-1131,1243-1318`; [fastpath.c] `src/mdrap/fastpath.c:592-597,798-803`; [C extension wrapper] `src/mdrap/_fastpath_c.c:84-91,116-125`.

`FastQualityEngine.evaluate()` calls the per-instance engine pointer while holding `_eval_lock` only when `thread_safe=True`; `close()` does not acquire that lock in either mode. The C `fastpath_engine_destroy()` frees the engine without coordinating with its per-engine lock. Thus a concurrent `close()` can free `self._engine_ptr` while `evaluate()` is entering or executing native code. The Stage 2 lifecycle lock protects only deprecated singleton exports (`fastpath_evaluate`, etc. around `fastpath.c:1244+`), not the per-instance API used by `FastQualityEngine`.

**Impact:** native use-after-free, process crash, or memory corruption during shutdown/reconfiguration concurrent with ingestion.  
**Reproduction:** run `FastQualityEngine.evaluate()` in one thread while repeatedly calling `close()` from another; current lifecycle test exercises singleton exports only (`tests/test_native_lifecycle_concurrency.py`).  
**Fix:** synchronize close, reset, batch evaluation, and scalar evaluation on one instance lifecycle lock; mark closed and make repeated close safe. Add an adversarial per-instance race test. Avoid freeing the pointer until in-flight calls drain.

### P2 — A stale lower sequence rewinds the tracking watermark

**Evidence:** [ws_feed.py] `src/mdrap/ws_feed.py:631-668`, specifically assignment at 644 before lower-sequence handling at 653-656; worker calls it without reset metadata at `960-970`.

`check_sequence_gap()` stores every incoming sequence before checking whether it is stale. A delayed/out-of-order old packet therefore moves `last_seq` backwards. The next normal packet is then reported as a gap and may inflate missing-event counts. The WebSocket worker supplies no `is_reset` signal, so a lower value from a delayed frame and a legitimate feed reset are indistinguishable here.

**Impact:** false gap alarms and unreliable sequence/reconciliation health after packet reordering.  
**Reproduction:** `100`, then stale `50`, then `101`; the final event is counted as a 50-event gap even though the stream continued from 100 to 101.  
**Fix:** keep the high-water sequence for stale packets; model confirmed reset/reconnect by a new epoch or explicit reset before updating the watermark. Test stale packets followed by contiguous traffic separately from genuine session resets.

## Reported P1 / Stage 2 fix verification

| Fix | Current assessment | Evidence and remaining issue |
|---|---|---|
| VULN-01 native singleton lifecycle | **Partial** | Singleton functions take `g_lifecycle_lock` across evaluation (`fastpath.c:1199-1310`), removing the reported singleton teardown window. Per-instance pointers remain unprotected as described above. Global spinlock also serializes singleton calls; assess that compatibility path separately if used concurrently. |
| VULN-02 archive traversal | **Source fix confirmed** | `archive.py:68-75` compares `os.path.commonpath`; sibling-prefix traversal is prevented. Cross-drive `commonpath` errors fail closed. |
| VULN-03 pure wheel binaries | **Source fix plausible; artifact unverified here** | `MANIFEST.in` excludes binary suffixes and `CleanBuildPy` removes them after `build_py` (`src/mdrap_build.py:35-48`). The checked-in wheel test failed because the sandbox denied reading `dist/mdrap_core-6.0.0-py3-none-any.whl`, not because a binary was observed in that artifact. Re-run against a freshly built wheel in CI. |
| VULN-04/VULN-08 DLQ accounting and persistent connection | **Fix confirmed with caveat** | Bounded deque overflow increments dropped count before append (`provenance.py:331-343`); repeated overflow test passed. Every recoverable persistence error still logs a warning (`334-338`), which can amplify CPU and disk I/O during a sustained DB lock. Rate-limit the log while preserving counters. |
| VULN-05 channel scoping | **Partial** | Channel is included in the sequence key and isolation test passes (`ws_feed.py:956-970`; focused test passed). Lower sequence values still rewind state (P2 above). |
| VULN-06 parallel WAL readers | **Correctness tests pass; scaling claim not supported** | Thread-local read connections are created and retained until Store close (`storage.py:452-474`). Existing checked-in forensic run reports 3,452 QPS at 1 reader, 6,253 at 2, 1,277 at 8, and 777 at 16; this bounded 2,000-row microbenchmark is not a production result, but it does not support unqualified “parallel reads scale” claims. |
| VULN-07 Windows SHM handle inheritance | **Source fix confirmed** | `mdrap_core.c:248-253` explicitly sets `bInheritHandle = FALSE`. |

## Actual performance and resource findings

### P2 — Pipeline work after the native quality check dominates measured Python path

**Evidence:** fresh cProfile, 3,000 simulator events, seed 42; call totals summarized above. Source: [pipeline.py] `src/mdrap/pipeline.py:731-810`; [reconciliation.py] `src/mdrap/reconciliation.py:746-806` and `323-350`.

The native quality kernel is not the dominant cost in this profile. Each event runs reconciliation, analytics/reliability/BBO/watchdog dispatch, journaling, and storage enqueue. Quote selection sorts candidate sources and allocates active quote/bid/ask dictionaries (`reconciliation.py:323-350`). This run includes the deprecated `Pipeline` and in-memory SQLite, so treat it as a locating profile, not an end-to-end production benchmark.

**Recommendation:** profile the current supported Engine/Projection path separately, then trim duplicate per-event work and avoid allocating/sorting quote candidate maps when a one-pass selection can preserve the same result. Compare single-event and batch paths with identical semantic checks.

### P2 — Repeated JSON encoding is a measurable hot-path cost

**Evidence:** cProfile attributed 0.088 s cumulative to 11,309 `json.dumps` calls; pipeline serializes canonical and lineage payloads at `770-810`, including event `to_dict()` for journal appends at `772-775` and raw payload conversion at `778-785`. `CanonicalEvent.to_dict()` is at `models.py:600`.

**Impact:** CPU and temporary string allocation per event; cost rises with raw payload size.  
**Recommendation:** identify values serialized more than once across journal, quarantine, and projection, then reuse canonical serialized representations where format and durability semantics match. Do not remove required audit serialization.

### P2 — SQLite reader connections retain large per-connection cache budgets

**Evidence:** `DEFAULT_SQLITE_CACHE_MB=64` and `DEFAULT_SQLITE_MMAP_MB=256` (`storage_schema.py:260-265`); each reader configures those values (`storage.py:458-467`) and is retained in `_all_local_conns` until Store.close (`469-471`, `1121-1129`).

**Impact:** memory/cache budget can multiply with worker thread count, and short-lived reader threads leave connections open for the Store lifetime. SQLite cache allocation is lazy, so 64 MB is a configured ceiling, not proof that every connection consumes it.  
**Recommendation:** choose cache sizing based on realistic concurrent readers; use a bounded reader pool or thread cleanup strategy, and verify RSS/fd counts under thread churn before retaining connection-per-thread.

### P2 — Provenance direct-SQLite path commits per event

**Evidence:** [provenance.py] `src/mdrap/provenance.py:242-282` wraps a single-row insert in a connection context for every record. Checked-in forensic data reports 2.917 ms average per record over only 100 records (`audit/v6.0.0/raw/forensic_benchmarks.json`).

**Impact:** per-event commit latency can dominate market-data processing when provenance persistence is synchronous.  
**Recommendation:** batch writes or queue them behind a bounded, durable writer with explicit acknowledgement/durability policy; keep retry and loss accounting intact. Re-measure durable p99 latency.

## Unnecessary code, dependencies, and architecture

| Component | Classification | Evidence / recommendation |
|---|---|---|
| `_CORE_MODULES` in `src/mdrap_build.py:8-33` | **REMOVE** | No references elsewhere in that file; `CleanBuildPy` only wipes build output and removes binaries. It is dead copied configuration and implies a filtering behavior that does not occur. |
| `config` and `yaml` optional extras in `pyproject.toml:55-60` | **MERGE** | They declare the same `pyyaml>=6.0` dependency. Keep one documented spelling or retain an alias only if external users rely on it. |
| `compat/` package | **KEEP** | Explicitly opt-in legacy top-level import shims (`compat/pyproject.toml`); it is not a core runtime dependency. Verify consumers before removal. |
| `contrib/` package | **KEEP** | Separately packaged extension modules with a core-module exclusion filter; not redundant with core packaging. |
| Native C fastpath | **KEEP / OPTIMIZE** | The existing forensic microbenchmark reports 669,855 vs 302,856 events/s for native vs Python quality evaluation on 10k events, but it does not isolate full-pipeline benefit. Fix object lifecycle first; prefer batch crossings and test end-to-end value. |
| Core dependencies | **KEEP current zero-mandatory model** | `pyproject.toml` declares no required third-party runtime dependencies; optional integrations are grouped. No optional dependency was removed because callers and fallback behavior require separate verification. |

The architecture is broad and has distinct legacy, analytical, extension, and compatibility surfaces, but evidence does not support a rewrite. The near-term simplification is to make one supported ingestion/projection path obvious, preserve `compat` as a separate opt-in distribution, and stop carrying duplicate packaging/configuration definitions. Confirm module-level import/use graphs before deleting more modules.

## Verification performed

- Focused hardening, sequence, provenance, and SQLite synchronization suites: **21 passed** (`pytest`, 5.85 s).
- A broader selected run produced **10 passed, 1 failed, 4 errors**: wheel inspection could not read the protected `dist/` artifact; four `tmp_path` fixtures could not create pytest temp folders under the sandbox. Those are environment access failures, not established product defects. The corrected focused suite used an in-workspace temp directory.
- Fresh cProfile run: 3,000 seeded simulator events; 767,162 calls, 0.799 s total profile time. Results are useful for relative hot spots only.
- Full test suite and a fresh wheel build were not completed. Do not treat prior checked-in “all tests pass” or wheel-purity claims as independently re-verified by this run.

## Recommended fix order

1. **P1:** Close the per-instance native engine use-after-free; test evaluate/reset/batch versus close.
2. **P2:** Preserve sequence high-water marks across stale/reordered packets; distinguish resets by epoch.
3. **P2:** Batch synchronous provenance persistence with explicit durability acknowledgements; cap repeated failure logging.
4. **P2:** Reduce per-event JSON conversion and reconciliation allocations after profiling the supported production path.
5. **P2:** Bound SQLite reader connection count and tune cache ceilings against measured RSS and throughput.
6. **P2:** Re-run artifact build and binary inspection in CI; ensure the test inspects a freshly built wheel rather than an inaccessible/stale `dist/` file.
7. **P3:** Remove the unused build constant and consolidate the duplicate YAML extra if compatibility permits.

