# MDRAP Phase 8 — Residual Risk Register

## 1. Risk Register & Mitigations

| Risk ID | Category | Risk Description | Severity | Current Mitigation / Workaround | Planned Next Action |
|---|---|---|---|---|---|
| **RISK-P8-01** | Hardware | Physical Solarflare Onload / DPDK kernel-bypass NIC hardware cannot be physically tested in current sandbox. | Medium | Software baseline and architecture validated; physical certification explicitly marked BLOCKED. | Procure dedicated Linux bare-metal server with SFN8522 dual-port 10GbE NIC. |
| **RISK-P8-02** | Connectivity | Direct exchange cross-connects and proprietary multicast feeds cannot be accessed without licensed DMA. | High | Full ITCH 5.0 and DBN parsers validated via PCAP replay; shadow validation runbook prepared. | Execute commercial exchange data agreement and arrange dark-fiber cross-connect. |
| **RISK-P8-03** | Concurrency | Windows IOCP socket loop scaling beyond 200+ clients under Windows vs Linux epoll. | Low | Decoupled `AsyncFanoutManager` maintains bounded deques; Linux epoll recommended for 500+ client production fleet. | Deploy Linux multi-process shard architecture in production. |
| **RISK-P8-04** | Deprecation | Clients continuing to use deprecated legacy import paths (`mdrap.options`, `mdrap.tca`). | Low | Backward-compatible import shims maintained with informative deprecation warnings across v3.x. | Remove legacy shims in MDRAP v4.0.0 after migration grace period. |
