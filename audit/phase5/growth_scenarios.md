# MDRAP Phase 5 — Growth Scenarios and Scalability Modeling

## 1. Executive Summary
This document provides empirical and analytical scalability models for the Market Data Reliability & Acceleration Platform (MDRAP). We analyze four growth vectors: **Instrument Universe Expansion**, **Feed Source Growth**, **Consumer Fanout**, and **Extreme Volatility Bursts**. For each scenario, we define system behavior, identify the primary architectural bottleneck, and specify the trigger point requiring horizontal partitioning or hardware expansion.

---

## 2. Growth Dimension 1: Instrument Universe Expansion

### Workload Progression
- **Baseline (Pilot)**: 20 active equity tickers (e.g., AAPL, MSFT, NVDA, SPY).
- **Expansion Tier 1**: S&P 500 universe (500 liquid equities).
- **Expansion Tier 2**: US Full Equities & OPRA Options (8,000 equities + 1,200,000 active options contracts).

### Impact Analysis
```
Symbol Universe: 20 symbols ──> 500 symbols ──> 8,000 symbols
Memory State:    0.2 MB     ──> 5.1 MB      ──> 82.0 MB
Dedup Index:     Fast Hash  ──> Fast Hash   ──> Partitioned Shards
```
- **Reconciler Cache (`self._latest`)**: Stored per `(instrument_id, feed_id)`. For 8,000 symbols across 3 feeds = 24,000 cached records. Total memory footprint: **~12 MB**.
- **Welford Rolling Anomaly Stats**: 8,000 symbols $\times 24$ bytes = **~192 KB**. Bounded and $O(1)$.
- **Bottleneck**: SQLite query index size when scanning historical ticks by instrument ID.
- **Remediation**: Partition SQLite tables by instrument prefix or utilize columnar storage (`src/columnar.py`) for historical partitions.

---

## 3. Growth Dimension 2: Feed Source Growth (Multi-Venue Ingress)

### Workload Progression
- **Baseline**: Dual feeds (Feed A: ITCH SBE, Feed B: Direct JSON).
- **Tier 1 (5 Feeds)**: NASDAQ ITCH, NYSE Pillar, BATS Pitch, CME MDP 3.0, CBOE.
- **Tier 2 (12 Feeds)**: Full US Lit Exchanges + 4 Dark Pool Aggregators + 3 Crypto Venues.

### Impact Analysis
- **Reconciliation Complexity**: $N$ feeds for instrument $I$ requires pairwise cross-feed deviation checks:
  $$\text{Comparisons per tick} = \mathcal{O}(N)$$
  For $N = 5$, comparison overhead is trivial (< 2 µs in Python, < 200 ns in C fastpath).
  For $N = 12$, linear scan across 12 feeds takes ~5 µs in Python.
- **Bottleneck**: Ingress thread contention if all feeds share a single socket listener.
- **Remediation**: Dedicated OS thread and socket buffer (`SO_RCVBUF = 16MB`) per venue adapter with zero-copy handoff into dedicated SPSC ring buffer channels.

---

## 4. Growth Dimension 3: Downstream Consumer Fanout

### Workload Progression
- **Baseline**: 2 internal consumers (1 via TCP socket, 1 via Shared Memory).
- **Tier 1**: 20 algorithmic trading desk consumers.
- **Tier 2**: 100+ enterprise quantitative consumers and reporting ledgers.

### Impact Analysis & Architectural Limits
```
Consumers:       2 Consumers ──────────> 20 Consumers ──────────> 100+ Consumers
Transport Mode:  Direct TCP/SHM         Direct TCP/SHM          SHM Broadcast / Multicast
Host CPU Load:   ~5% single core        ~25% multi-core         Socket starvation
```
- **Shared Memory (SHM)**: Multiple readers can read from a lock-free seqlock SHM ring buffer without imposing additional write overhead on the producer. Fanout scaling from 1 to 20 local consumers imposes **0% latency penalty** on the engine core.
- **TCP Sockets**: For out-of-process or remote consumers, transmitting 10,000 eps $\times 64$ bytes = 640 KB/sec per consumer. For 100 consumers, outgoing socket bandwidth is 64 MB/sec (512 Mbps). The kernel socket write buffer becomes a bottleneck under slow-consumer conditions.
- **Bottleneck**: Slow TCP consumer blocking or exhausting send buffers.
- **Remediation**: Non-blocking asynchronous event loop (`asyncio` / epoll) with explicit client disconnect triggers when send queue reaches capacity (`MAX_CONSUMER_BACKLOG = 50,000`).

---

## 5. Growth Dimension 4: Volatility Spikes and Market Open Surges

### Workload Progression
- **Normal Trading**: 2,000 – 4,000 eps steady state.
- **Market Open (09:30:00 EST)**: 25,000 – 50,000 eps burst for 180 seconds (12.5x spike).
- **Macro Economic Print / Flash Event**: 100,000+ eps burst for 30 seconds (25x spike).

### Resilience Behavior
- **Ring Buffer Headroom**: SPSC ring buffer size = 1,048,576 slots. At 50,000 eps, ring buffer provides **20.9 seconds of absorbing cushion** if the disk persistence layer temporarily pauses.
- **Grouped Fsync Dynamic Elasticity**: As queue depth rises above 5,000 events, `fsync_policy="grouped_by_size"` automatically batches writes into 64 KB chunks, increasing I/O efficiency proportionally to load.
- **Graceful Shedding**: Under catastrophic overload (> 200,000 eps sustained), non-trade quote ticks are downgraded to L1 top-of-book updates while trade execution reports retain 100% processing priority.

---

## 6. Horizontal Scaling Decision Matrix

| Metric Threshold | Single Node (Profile A) | Required Scale-Out Action |
| :--- | :--- | :--- |
| **Ingest Rate** | $\le 15,000\text{ eps}$ | Partition feeds across dedicated ingress sidecars. |
| **Active Instruments** | $\le 1,000$ | Instrument sharding across hash-partitioned MDRAP nodes. |
| **Consumer Count** | $\le 25$ | Deploy SBE distribution proxies / kernel-bypass multicast. |
| **Storage Ingest** | $\le 100\text{ GB/day}$ | Ship compressed segment archives to centralized Ceph / S3 lake. |
