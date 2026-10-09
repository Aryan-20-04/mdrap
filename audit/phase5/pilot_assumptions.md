# Phase 5 Pilot Assumptions & Constraints

**Date**: 2026-10-09  
**Platform Version**: MDRAP v3.0.0  

---

## 1. Baseline Assumptions

1. **Host Isolation**: The pilot runs on dedicated compute cores without competing background compilation or heavy virtual machine contention.
2. **Clock Monotonicity**: System time is backed by a monotonic high-resolution clock (`CLOCK_MONOTONIC` / QPC) with drift < 1ms/hour.
3. **Storage Latency**: Disk backing the IngestLog WAL supports sequential writes with fsync latency < 10ms.
4. **Network Boundaries**: Inbound feed emulation occurs locally over loopback (`127.0.0.1`), eliminating transit packet loss.

---

## 2. Explicit Constraints & Scope Limits

- **Simulation Disclosure**: The pilot uses deterministic synthetic replay frames with realistic price/sequence dynamics. It does not represent live market liquidity from registered exchanges.
- **Failover Limitations**: Profile B active-passive failover is validated via synthetic heartbeat interruption in local test harnesses; live cross-datacenter multi-homed BGP routing is out of scope.
- **Licensing Restrictions**: Usage accounting tracks simulated internal desk consumption for capacity validation only. No customer billing charges are generated.
