<USER_REQUEST>
# MDRAP Foundation Audit, Round 2 (Remediated Tree)

## 0. How to read this document (for an LLM or a human)

- This document restates an external audit report of the project **mdrap**. It adds no new findings.
- Every claim carries an **evidence label** (see section 2). Do not upgrade a `[read]` or `[extrap]` claim to a measured fact.
- Where the source report does not state something (for example, a scoring formula or the exact meaning of an earlier finding ID such as E3), this document says so and does not guess.
- Nothing here was re-executed by the author of this file. All `[ran]` results are the auditor's results.
- Goal of this file: let a reader understand **(a) what state the project is in, (b) why the score is 4/10, and (c) exactly what needs to change, in what order.**

## 1. Project snapshot (facts from the report)

| Item | Fact |
|---|---|
| Subject of audit | The new tree in `mdrap.zip`, treated as the subject; every earlier experiment was re-run on it, unmodified except for the path |
| Relation to earlier audit | Same project as the first report. 26 source files changed, plus a new `tests/test_audit_remediation.py`. Looks like remediation after the first report |
| Version string | Still `3.0.0`. **Two different builds carry the same version** |
| Test results, new tree | 986 passed, 32 skipped, 1 failed |
| Test results, before | 977 passed, 32 skipped, 1 failed |
| The 1 failure | Same on both trees, environmental: it needs the package installed so entry points exist |
| Project's own claim | 1,022 passed |
| Two ingest paths | **Engine** (the hardened core, uses `IngestLog`, the write-ahead log or WAL) and **Pipeline** (older path with its own journal, emits a deprecation warning pointing at the Engine) |
| Shipped/production path | The Docker image runs `mdrap serve`, which goes API → Pipeline. No CLI module references `IngestLog` |
| Earlier finding IDs mentioned | E1, E2, E4, E5, E6 (Engine Critical/High findings) are genuinely fixed on the new tree. The report does not describe E3 |

## 2. Evidence labels

| Label | Meaning |
|---|---|
| `[ran]` | The auditor executed it and saw the result |
| `[read]` | Code reading only; not executed |
| `[extrap]` | Arithmetic extrapolated from a measurement |
| `[ran, forced]` | Executed, but the failure was injected by the auditor, so it shows the handler's behavior, not how often it fires |

## 3. What the remediation fixed

| Finding | v3.0.0 | New tree |
|---|---|---|
| Poison-pill event bricks the Engine | bricked | Fixed: quarantined, reopens cleanly `[ran]` |
| Replay changes decisions (timestamp not stamped before append) | diverged | Fixed for decisions `[ran]` (but see N1) |
| Dedup false positives (venue and quantity ignored) | 2 of 2 wrong | Fixed `[ran]` |
| INVALID events written to `canonical_events` | yes | Fixed: quarantine only `[ran]` |
| Non-canonical reason codes | 3 invented | Fixed `[ran]` |
| Torn segment header bricks the WAL | bricked | Fixed `[ran]` |
| Mid-segment bit flip silently destroys 70% of a segment | destroyed | Now fails loudly, file untouched `[ran]` |
| Live vs replay divergence with forced preemption | 122/400 | 0/400 (engine lock added) `[ran]` |
| Security-rejection `event_id=""` collapse | 198 → 1 row | Fixed: 198 rows, 0 conflicts `[ran]` |
| `OverflowError` escapes `process_batch` | escaped | Fixed `[ran]` |
| No flush timer, tail stuck in RAM | 0 rows after 2 s | 5 rows after 2 s `[ran]` |
| Torn journal tail re-quarantined every restart | 1, 2, 3, 4 | Stays at 1 `[ran]` |
| Reason registry pollution; lossy options/bond serialization; empty trade parsed as VALID; `reasons="STALE"` split into characters; durability typo | all present | All fixed `[ran]` |
| Auth lockout evaded by varying token prefix | evadable | Now IP-keyed (see trade-off in M2) `[read]` |
| Unlocked `process_batch` from concurrent API requests | no lock | `pipeline_lock` added `[read]` |

Auditor's assessment: the work is solid and well targeted. The new tests pass. The Engine went from "can be bricked by one message" to a defensible design in one pass.

## 4. Problems the remediation introduced or left in its fixes

### N1: Determinism regression. Severity: High. `[ran]`
- Live event IDs are now `raw_A_0`; replayed IDs are `evt_A_X_0`, for **every unsequenced event**.
- Cause: `submit()` assigns `raw_id` **after** `log.append()`, so the WAL never contains it.
- In v3.0.0 these agreed. The timestamp fix for determinism broke determinism for IDs.
- Impact: any rebuild-from-zero (a new projection, a lost checkpoint) produces rows that do not match what was originally emitted.
- Why the new tests missed it: they assert that each fix works; they do not assert that **live equals replay**.

### N3: State-inconsistent catch-all. Severity: Medium. `[ran, forced]` plus `[read]`
- `step()` now wraps everything in `except Exception` and increments counters again in the handler.
- With a failure injected after the inner function ran, 2 events gave `event_count=3`.
- The failure was forced, so this shows the handler is inconsistent, not how often it fires.
- It also logs a warning per poison event, which is a log-flood vector `[read]`.

### N4: No size cap at the Engine boundary. Severity: Medium. `[ran]`
- A 50 MB payload was accepted into the WAL and held the engine lock for 1.5 s.
- The API enforces limits; embedders using Engine directly get none.

### N5: Lossy "fix" for lone surrogates. Severity: Low. `[ran]`
- Payloads are now written with `errors="replace"`, so the "immutable raw record" holds `?` where the peer sent a lone surrogate.
- Safe, but should be a documented decision, not a silent one.

### N2: Gap, not a bug. `[ran]`
- With no sequence and no `exchange_ts`, an exact retransmit passes as VALID.
- There is no message-ID or trade-ID concept, which is what real feeds dedupe on.

## 5. Still open

### Critical

**C-A: The hardened core is not the shipped path.**
- `[read]` The Docker image runs `mdrap serve` → API → Pipeline. No CLI module references `IngestLog`. Pipeline still emits a deprecation warning pointing at the Engine.
- Therefore all WAL fixes protect nothing that production traffic touches.
- Pipeline's journal still uses `flush()` with no `fsync` `[ran]`.
- The journal is never truncated at runtime and is now 1,248 B/event, 26% larger than before `[ran]`. About 1,078 GB/day at 10k events/s `[extrap]`.
- The glued-record bug (a fresh record fused to a torn fragment) is unchanged `[ran]`.
- An HTTP 200 on `/v1/ingest` still means only "enqueued".

**C-B: WAL durability primitives are untouched `[ran]` unless noted.**
- A failed `fsync` leaves an offset gap and the log keeps writing.
- Two processes can open the same log; the second truncated a simulated in-flight frame.
- Source text for the next item reads: "`grouped_by_time` and `never` lose acknowledged events on `kill -9`: 44 of 50 recovered". The source wording is slightly garbled; the stated cause is that the userspace buffer is never flushed, so even a process crash loses data.
- `[read]` There is no directory `fsync`, and the frame CRC covers only the payload, not the header.
- The new fail-loud corruption policy has no salvage or repair tool, so a single flipped bit now means a manual hex-editing session.

### High
- **No group commit** `[ran]`: with `always`, batch=100 gives 6.3K events/s versus 5.5K for batch=1, so a "batch" is still one fsync per event.
- **Startup is O(history)** `[ran]`: restart took 2.8 s for 160K events (about 17 µs/event), scaling linearly. Snapshot and retention methods exist but nothing calls them. The old "replay twice" problem is only partly fixed. About 4 hours of replay per day of log at 10k ev/s `[extrap]`.
- **Extensibility is still declarative** `[read]`: no core path consumes the six plugin groups; `OutputSink.broadcast_tick` is never called.
- **Name collision** `[ran]`: `mdrap.MarketEvent` is still the client's class, not `models.MarketEvent`.
- **Prices are still float** `[ran]`; `from_dict` still raises on bad input and accepts NaN.

### Medium
- **M1. Path traversal in `RawArchive`** `[ran]`: both `../../../x` and absolute `source` values write outside `base_dir`. The module is deprecated and the shipped API does not wire it in (only the CLI does, with simulated sources), so this is latent. It is real for any embedder passing external feed names.
- **M2. Auth lockout trade-off** `[read]`: IP-only keying fixes prefix evasion, but anyone sharing an IP can now lock out legitimate clients behind it. A successful login also resets the counter.
- **M3. `HashedKeyStore` subclasses `dict`** `[read]` and overloads `get`/`in`/`[]` to accept either a token or a stored hash. Authentication uses the safe `get_by_token`, so it is not exploitable today, but any future `.get()` call would authenticate on a leaked hash. The unsalted legacy SHA-256 fallback has no migration path.
- **M4. Entitlements are coarse** `[read]`: no read-side per-venue or per-instrument licensing; WebSocket revocation is checked only at broadcast time.
- **M5. Test anchoring**: 107 of 171 test files import via the deprecated flat shims. One new test has a typo'd dict key (`"source=" : "TEST"`) that the test never notices.

### Unproven (do not treat as bugs)
- The `Pipeline.flush()` batch-list swap now runs from the writer thread too. The auditor could not trigger a lost event across 120K events on either tree, and the concurrent-API test (8,000 events, 8 clients) was clean on both trees.

## 6. Areas not fully assessed
- Observability: saw `/v1/health`, an authenticated `/metrics`, and `Engine.metrics()` with `log_lag` hardcoded to 0; no real audit.
- Not examined: `ws_feed` and the adapters, full `quality.py` and C-kernel semantics, and whether `docs/` still matches the code.
- Docs remain stale in places; benchmark figures in the docs were not re-run.

## 7. What is done well
- The README is candid.
- Store has real migrations with downgrade protection.
- The salt is required outside demo mode.
- The C kernel has an ABI handshake and bounds checks.
- Deployment: multi-stage Docker build, non-root user, healthcheck, required secrets, CodeQL, sanitizer and wheel CI.
- The remediation itself was fast and mostly precise.

## 8. Comparison with mature systems (concepts only, not to copy)
- Kafka and Postgres checksum the header as well as the payload.
- Postgres treats a failed fsync as fatal, not retryable, and fsyncs directories.
- Kafka's group commit and sparse offset index avoid per-event fsync and O(N) scans.
- SQLite and Postgres refuse concurrent openers with a file lock.
- Those projects also ship repair tooling for the corruption cases they fail loudly on.

## 9. Target ("ideal") shape
1. One core, thin edges. **Engine is the only ingest path.**
2. Input is stamped (timestamp **and** ID) **before** the append.
3. `step()` is total (never leaves state half-updated).
4. WAL with header+payload CRC, directory fsync, poison-on-fsync-failure, a file lock, and group commit.
5. Snapshots plus segment retention.
6. Projections as replaceable sinks.
7. Storage stays a narrow interface (events in, batch out), not a 14-method analytics contract.
8. Only consumed extension points stay public: storage sink, auth provider, adapters. Delete the declarative ones until something uses them.
9. Quality rules stay config-driven constants, with a **live-equals-replay property test** over event IDs, decisions and counts.

## 10. Roadmap

| Phase | Item | Problem → fix | Trade-off | Priority / effort | Files | Breaking? |
|---|---|---|---|---|---|---|
| 1 | Fix N1 | Stamp `raw_id` before append; add a live-equals-replay property test (IDs, decisions, counts) | none | Critical / S | engine.py, tests | No |
| 1 | Fix `step()` handler (N3) | Snapshot-and-restore state on exception; stop double counting | tiny copy cost | High / S | engine.py | No |
| 1 | Archive traversal (M1) | Validate `source` against a safe pattern and resolve paths | stricter names | Medium / S | archive.py | Mild |
| 1 | Event size cap (N4) | Reject or quarantine over-limit payloads at `Engine.submit` | limit must be configurable | Medium / S | engine.py | No |
| 2 | Route API, CLI and container through Engine | The shipped path bypasses the WAL; retire Pipeline's journal | migration effort, behaviour diffs | Critical / L | api.py, service.py, cli/*, Docker | Yes (deprecation → removal) |
| 2 | WAL primitives | Header CRC, dir-fsync, poison on fsync failure, file lock, flush userspace buffer on ack | slight throughput cost | Critical / M | ingestlog.py | Segment format bump (versioned) |
| 2 | Salvage tool | `mdrap wal verify` / `salvage` for the new fail-loud policy | needs careful design | High / M | cli/, ingestlog.py | No |
| 3 | Group commit and offset index | Remove per-event fsync, avoid O(N) restart | more complexity | High / L | ingestlog.py, engine.py | No |
| 3 | Snapshots and retention wired in | Bound startup and disk | snapshot versioning | High / M | engine.py | No |
| 4 | Money type | float → fixed-point or decimal | storage and API churn | High / L | models.py, storage.py, projection.py | Yes (major) |
| 4 | Tests off the shims | Move 107 files to `mdrap.*`, make the suite hermetic | mechanical | Medium / M | tests/ | No |
| 5 | Wire or delete plugin groups | Only keep points the core consumes; typed storage rows | smaller surface | High / M | plugins.py, protocols.py | Yes (pre-1.0 window) |
| 5 | Auth provider contract | Make `AuthProvider` match what the API calls | wider protocol | Medium / S | protocols.py, api.py | Mild |
| 6 | Versioning discipline | Bump versions per build, stamp the commit, publish a stability policy and deprecation windows, version the segment and journal formats | process overhead | High / S | _version.py, CI | No |
| 6 | Docs reset | Replace stale architecture claims with measured guarantees and non-guarantees | needs re-benchmarking | Medium / M | docs/ | No |

Effort key as used in the report: S = small, M = medium, L = large.

## 11. Production-readiness checklist (current status)

- [x] Containerised, non-root, healthcheck, required secrets
- [x] Schema migrations
- [ ] Durable ack on the shipped path
- [ ] Crash-safe WAL under fsync failure and concurrent openers
- [ ] Bounded restart and disk
- [ ] Repair tooling
- [ ] Money type
- [ ] Real observability
- [ ] Load and failure-injection evidence on the shipped path

## 12. Verdict and score

**Foundation Score: 4/10** (up from about 3, as stated in this report). **Confidence: moderate** (observability, `ws_feed` and docs fidelity were not audited).

| Area | Score | Area | Score |
|---|---|---|---|
| Architecture | 4 | Documentation | 4 |
| Reliability | 4 | Testing | 5 |
| Performance | 4 | Production readiness | 3 |
| Security | 6 | Ecosystem potential | 3 |
| API design | 3 | Developer experience | 4 |
| Extensibility | 2 | | |

### Why the score is what it is
The report gives no scoring formula. The mapping below only groups the report's own facts under each area, so a reader can see what pulls each score down or up. It is an organizing aid, not an additional claim.

- **Overall (4):** The Engine is now defensible (section 3), but the shipped path does not use it (C-A), WAL durability primitives are untouched (C-B), and a new determinism regression exists (N1).
- **Architecture (4):** Two ingest paths; production uses the deprecated one (C-A). `mdrap.MarketEvent` name collision. Plugin groups are declarative only.
- **Reliability (4):** Poison-pill, torn-header, and bit-flip handling fixed; but failed fsync leaves an offset gap, concurrent openers are allowed, userspace buffer is not flushed on ack (44/50 recovered), no repair tool, N1 and N3.
- **Performance (4):** No group commit (6.3K vs 5.5K events/s). Startup is O(history) (2.8 s for 160K events). Journal is 1,248 B/event and never truncated.
- **Security (6, the highest):** Salt required outside demo mode, auth lockout now IP-keyed, hashed key store, CodeQL in CI. Held back by shared-IP lockout trade-off, `HashedKeyStore` dict overloading, unsalted legacy SHA-256 fallback, coarse entitlements, latent path traversal.
- **API design (3):** HTTP 200 means only "enqueued"; Engine boundary has no size cap; `AuthProvider` does not match what the API calls (roadmap Phase 5); prices are float and `from_dict` raises on bad input and accepts NaN.
- **Extensibility (2, the lowest):** No core path consumes the six plugin groups; `OutputSink.broadcast_tick` is never called.
- **Documentation (4):** Candid README, but docs stale in places and benchmarks not re-run.
- **Testing (5):** 986 passed; but tests assert each fix works rather than live-equals-replay, 107 of 171 files import via deprecated shims, one test has an unnoticed typo'd key, one environmental failure.
- **Production readiness (3):** Seven of nine checklist items unchecked (section 11); no durable ack on the shipped path.
- **Ecosystem potential (3) and Developer experience (4):** The report gives no separate breakdown for these two. Related facts: two builds share version 3.0.0, and the deprecated shims anchor most tests.

## 13. The single most important thing to change first

**Order of work:**
1. **Make live-equals-replay a tested property, starting with N1.** Stamp `raw_id` before `log.append()`; add a property test asserting that event IDs, decisions and counts from live processing equal those from replaying the WAL.
2. **Then route the shipped API and container through Engine** (Phase 2), retiring Pipeline's journal.

**Why this order:**
- N1 shows that point fixes regress without a guard on the invariant. The Engine's whole value is "replay reproduces live", and nothing currently guards that.
- The container still runs Pipeline, whose journal has no fsync, never truncates, and glues records. Every WAL hardening done so far protects nothing production traffic touches until the switch is made.
- The N1 fix plus the property test is small, and the auditor already has the reproduction.

**Done-when for the first work item (derived directly from the report):**
- Live event IDs equal replayed IDs for unsequenced events (currently `raw_A_0` vs `evt_A_X_0`).
- A test fails if live IDs, decisions or counts differ from replay.
</USER_REQUEST>
<ADDITIONAL_METADATA>
The current local time is: 2026-10-08T19:41:52+05:30.
</ADDITIONAL_METADATA>