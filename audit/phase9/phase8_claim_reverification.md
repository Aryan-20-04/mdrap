# MDRAP Phase 9 — Phase 8 Claim Re-Verification & Empirical Audit

## 1. Audit Scope & Specific Scrutiny
In accordance with Phase 9 Section 2 guidelines, every empirical claim reported in Phase 8 was independently audited to establish what was actually measured versus what architectural boundaries apply.

## 2. Re-Verification Analysis

### 2.1 Consensus & Failover Latency Claim Scrutiny
- **Phase 8 Claim**: "Failover transition latency: $p50 = 0.002\text{ ms}$ ($2.0\text{ \mu s}$), $p99 = 0.009\text{ ms}$ ($9.0\text{ \mu s}$)."
- **Re-Verification Finding**:
  - The Phase 8 benchmark (`benchmarks/failover_benchmark.py`) measured the in-memory execution time of `coord_s.request_leadership()` and `writer.validate_write(token_s)`.
  - In a single Python process, updating an integer epoch and creating a dataclass token takes $2.0\text{ \mu s}$.
  - **Critical Scrutiny**: This is **NOT** complete distributed multi-node failover time. A genuine failover includes:
    1. *Failure Detection Interval*: Primary heartbeat silence detection or lease expiration window (typically 200–500 ms).
    2. *Coordination Message Transit*: Network round-trips between quorum nodes (0.2–2.0 ms on LAN).
    3. *Fencing & State Recovery*: Journal tail inspection and fencing token broadcast.
    4. *Downstream Reconnection*: Client socket reconnect and TCP handshake (1–10 ms).
  - **Phase 9 Corrective Action**: Phase 9 implements a multi-process failover harness (`tests/test_multiprocess_ha.py`) that includes the actual failure detection timeout and socket reconnection, reporting true complete failover latency.

### 2.2 Consumer Fan-Out Delivery Claim Scrutiny
- **Phase 8 Claim**: "100 Clients: 797,770 egress frames/sec, publisher handoff $p50 = 0.70\text{ \mu s}$, 100.0% delivery."
- **Re-Verification Finding**:
  - The benchmark measured publisher enqueue into `AsyncFanoutManager._incoming_queue` and reader threads popping from `session.queue`.
  - The delivery boundary was the in-memory deque pop (`mgr.consume_event`).
  - **Critical Scrutiny**: Delivering frames into in-memory queues tests internal data structure dispatch. Delivering frames over TCP sockets involves Winsock kernel buffers, TCP window updates, and socket serialization.
  - **Phase 9 Corrective Action**: Phase 9 runs real multi-process TCP socket consumers (`StreamClient`) across varying concurrency tiers to measure physical socket delivery rates.

### 2.3 Companion Package Extraction Scrutiny
- **Phase 8 Claim**: Clean package modularization of `mdrap-options`, `mdrap-analytics`, `mdrap-strategies`, and `mdrap-contrib-vessel`.
- **Re-Verification Finding**:
  - Packages exist under `packages/` with standalone `pyproject.toml` files.
  - **Scrutiny**: Clean installation outside the repository checkout must be verified via isolated `pip install` builds without local repository path assumptions. Phase 9 executes clean standalone wheel builds and installs.

### 2.4 Physical Hardware & Live Feed Scrutiny
- **Phase 8 Claim**: Hardware bypass and live connectivity marked as limitations / pre-production gates.
- **Re-Verification Finding**: Confirmed. Physical Solarflare NICs and live cross-connects are genuinely absent from this host. Documented correctly as **BLOCKED / ENVIRONMENT-LIMITED**.
