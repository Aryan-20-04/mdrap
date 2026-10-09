# MDRAP Phase 3 — Known Risks & Mitigation Register

**Document Identifier**: `MDRAP-RISKS-P3-001`  
**Date**: October 9, 2026  

---

## 1. Architectural & Operational Risks

| Risk ID | Category | Description | Severity | Mitigation Strategy |
|---|---|---|---|---|
| **RISK-P3-01** | Toolchain Availability | Host environment lacks `cargo`/`rustc`. Rust SDK compilation cannot be executed on current Windows host. | MEDIUM | Deliver complete idiomatic Rust crate (`Cargo.toml`, typed structs, consumer, examples). Mark compilation validation as deferred pending cargo installation. |
| **RISK-P3-02** | Kernel-Bypass Feasibility | Windows host and typical cloud VM environments lack dedicated AF_XDP / DPDK NIC drivers. | HIGH | Conduct rigorous evaluation. Keep conventional socket + SHM IPC path as the production standard. Scaffold gated AF_XDP backend with honest limitation notes. |
| **RISK-P3-03** | Split-Brain in HA | Network partition between active and passive nodes could cause concurrent authoritative writers. | CRITICAL | Implement monotonic generation tokens (epochs) and fencing tokens. Writer must verify valid fencing token before appending to WAL. |
| **RISK-P3-04** | Undercounting in Metering | Crashes or restarts during consumer usage accounting could lose billing records. | HIGH | Store usage aggregates durably in SQLite with idempotent accounting keys before flushing distribution batches. |
| **RISK-P3-05** | Native Memory Safety | C++ consumer reading shared memory could read torn frames or dereference invalid pointers during writer overrun. | HIGH | Enforce Seqlock protocol (odd sequence = write in progress; even sequence = stable frame) with memory fences and atomic loads. |
