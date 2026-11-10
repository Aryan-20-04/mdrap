# MDRAP Strategic Reset — Remaining Risks Register

**Date**: 2026-11-11  
**Status**: ACTIVE RISK REGISTER  

---

## 1. Technical Risks

| Risk ID | Severity | Probability | Risk Summary | Mitigation Strategy |
| :--- | :--- | :--- | :--- | :--- |
| **TR-01** | Medium | High | **Exchange WebSocket Wire Mutation**: Crypto exchanges frequently alter JSON schemas without semver increments. | Maintain `ConnectorCertificationSuite` in CI running against recorded wire captures. |
| **TR-02** | Medium | Medium | **High-Frequency Memory Bloat under Burst**: Ingesting 100,000 msgs/s for hours can cause SQLite memory growth if checkpointing is delayed. | `storage.py` enforces auto-WAL checkpointing every 10,000 pages and bounded tombstone ring buffers. |
| **TR-03** | Low | Low | **Host Clock Jumps (NTP Step)**: If the host VM steps backward by 2 seconds, local receive timestamps can invert. | Use Windows QPC high-res monotonic clock or Linux `CLOCK_MONOTONIC_RAW` in hot paths. |

---

## 2. Business & Market Risks

| Risk ID | Severity | Probability | Risk Summary | Mitigation Strategy |
| :--- | :--- | :--- | :--- | :--- |
| **BR-01** | High | Medium | **"Not-Invented-Here" (NIH) Syndrome**: Prop desks often have engineers who believe they can build an assurance sidecar in a weekend. | Emphasize the microsecond C fastpath, RFC 6962 Merkle compliance proofs, and 8-drill chaos conformance suites that take months to build. |
| **BR-02** | Medium | High | **Sidecar Latency Concerns**: Traders fear any sidecar might slow down order routing. | Demonstrate passive tap mode: MDRAP runs out-of-band off a mirrored network tap or shared-memory reader with 0ns impact on order routing. |
| **BR-03** | Medium | Low | **Exchange API Key Security Concerns**: Clients worry third-party tools might expose trading credentials. | MDRAP never requires or accepts trading private keys; it strictly ingests public market-data feeds. |
