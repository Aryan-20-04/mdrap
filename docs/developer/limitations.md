# Known Limitations & Operational Boundaries

To maintain engineering integrity, MDRAP documents its explicit technical boundaries, architectural trade-offs, and operating limits.

---

## 1. What MDRAP Is NOT

- **Not an Order Management System (OMS)**: MDRAP cannot route orders, manage fills, or track trade executions.
- **Not an Execution Management System (EMS)**: MDRAP provides zero smart order routing (SOR) or algorithmic execution logic.
- **Not a General-Purpose Time-Series Database**: MDRAP is an active assurance sidecar, not an archival replacement for kdb+, InfluxDB, or ClickHouse. While it persists event corridors in SQLite for incident investigation, historical analytics spanning decades should be offloaded to dedicated lakehouses.
- **Not a Trading Strategy Backtesting Platform**: Although MDRAP includes deterministic corridor replay under `VirtualClock`, its purpose is forensic root-cause analysis, not portfolio optimization or multi-year strategy backtesting.

---

## 2. Throughput & Latency Boundaries

- **Pure-Python Fallback Engine**:
  - Validated throughput: ~25,000 to 50,000 events/second per core.
  - Evaluation latency: ~15 to 30 microseconds per tick.
- **Native C Vectorized Hot Path**:
  - Validated throughput: ~250,000 to 500,000 events/second per core.
  - Evaluation latency: ~1 to 3 microseconds per tick.
- **Hardware Multicast / Kernel-Bypass (Solarflare OpenOnload / DPDK)**:
  - While MDRAP supports passive socket taps, it does not currently provide custom kernel-bypass drivers. Desks requiring sub-microsecond hardware FPGA packet slicing should deploy hardware NIC filters upstream.

---

## 3. Platform & OS Specifics

- **Windows vs. Linux Shared Memory**:
  - On Linux/POSIX, shared memory uses `shm_open` with strict `0600` permissions.
  - On Windows, shared memory uses Windows Named Shared Memory (`CreateFileMappingW`).
- **SQLite WAL Write Concurrency**:
  - SQLite WAL mode allows unlimited concurrent readers alongside a single serialized writer.
  - While batching achieves tens of thousands of writes per second, extremely high-volume desks ($>100,000$ continuous ticks/sec) should enable ring-buffer deduplication or configure RAM disk (`tmpfs`) staging.

---

## 4. Market Microstructure Nuances

- **Crossed Markets Across Fragmented Venues**:
  - Cross-venue crossed spreads (e.g. Binance bid $>$ Coinbase ask) can occur legitimately during high volatility due to geographic latency between Tokyo, London, and New York. MDRAP flags these as `SUSPICIOUS` for cross-venue arbitrage evaluation, not automatically as exchange data corruption.
