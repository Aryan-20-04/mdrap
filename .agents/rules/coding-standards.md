# MDRAP Coding Standards & Constraints

Apply these constraints to all code generation and refactoring in this repository:

1. **Synchronous Pipeline (V1)**: Keep the pipeline single-process and synchronous. Do not introduce asyncio, threads, or multiprocessing into the core pipeline path.
2. **Deterministic Identifiers**: Never use `uuid.uuid4()` in the hot path. Use monotonic atomic counters (`itertools.count()`) prefixed by stream component (e.g. `raw-1`, `evt-1`).
3. **Non-destructive Quality Marking**: Never overwrite `INVALID` with `SUSPICIOUS`. Always enforce status priority.
4. **Segregated Batched Persistence**:
   - `canonical_events` must never receive `INVALID` events.
   - All database writes must use batched `executemany` commits.
5. **Memory Management**: Keep metrics windows bounded during real-time streaming; only allocate full histories when requested for end-of-run summaries.
6. **Cross-Platform Compatibility**: Do not use POSIX-only APIs (such as `os.uname()`). Use standard `sys.platform`.
7. **Regression Guard**: All changes must pass `python -m pytest tests/ -v` cleanly.
