# MDRAP Phase 7 — Platform Failure Taxonomy & Mitigation Matrix

## 1. Executive Summary & Objective
To prevent operational incidents from repeating and ensure deterministic operator responses during live market emergencies, this document establishes the comprehensive **MDRAP Failure Taxonomy**.

Each failure mode is classified with its detection mechanism, system impact, automatic containment, recovery procedure, and post-incident verification method.

---

## 2. Definitive Failure Taxonomy Matrix

| Failure Mode | Detection Signal | Operational Impact | Automatic Containment | Recovery Procedure | Verification Method |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **FM-01: Ingress Feed Drop** | Socket EOF / `ConnectionReset` | Ingress tick flow stalls on venue | Ingest gateway triggers exponential backoff (100ms..5s) | Re-establish TCP session; request sequence gap fill | Monotonic sequence check across reconnect boundary |
| **FM-02: Sequence Gap** | `seq > expected_seq + 1` | Temporary order book freeze | Quarantine incoming event with `SEQUENCE_GAP` flag | IngestLog replay / vendor snapshot sync | Verify zero corrupted book states published |
| **FM-03: Invalid Input Payload** | `SchemaError` in `gateway.py` | Potential parser crash if uncaught | Isolate payload; route to quarantine table with bitmask | Log warning; drop counter incremented | Quarantine table row inspection; zero pipeline crash |
| **FM-04: Persistence Stall** | SQLite `OperationalError: database locked` | Backpressure in drain queue | IngestLog WAL continues atomic appends; memory ceiling protected | WAL checkpoint; vacuum; disk space inspection | Drain queue clears to 0; disk IOPS restored |
| **FM-05: Consumer Lag / Stalled TCP** | Queue occupancy $> 90\%$ | Potential engine backpressure | Evict consumer upon $\ge 10$ dropped frames | Client socket closed; client reconnects | Engine throughput unaffected; zero engine stall |
| **FM-06: Process Crash** | Missing heartbeat / SIGKILL | Shard stops publishing ticks | OS cleans up `shard.lock`; standby acquires lock | Replay local WAL; resume at sequence $N+1$ | Recovery completes in $< 2.0\text{ s}$; RTO verified |
| **FM-07: Fencing Split-Brain** | Secondary acquires `shard.lock` collision | Two writers claiming partition | Second process fails closed immediately with code `42` | Terminate duplicate process | Inspect WAL files for 0 divergence |
| **FM-08: Memory Saturation** | RSS exceeds configured ceiling | Risk of OS OOM killer | Bounded deques and LRUs drop oldest or reject requests | Free cached historical data; trigger GC | RSS delta stabilizes within configured budget |
