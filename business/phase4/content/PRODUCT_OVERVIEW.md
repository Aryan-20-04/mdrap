# MDRAP Product Overview: Market Data Reliability & Assurance Platform

**Release**: `v6.0.0`  
**Classification**: Institutional Market-Data Reliability Sidecar  
**Audience**: Chief Technology Officers, Heads of Quantitative Trading, Risk Officers, Infrastructure Architects  

---

## Executive Summary

The **Market Data Reliability & Assurance Platform (MDRAP)** is an independent, read-only sidecar engineered to eliminate the hidden market data failures that degrade algorithmic trading strategies and trigger execution losses.

Modern quantitative trading firms invest millions into pricing engines, order execution gateways, and low-latency network routes. Yet almost all trading strategies consume market data feeds without real-time cryptographic validation. When exchanges drop packets during volatility bursts, delay order cancellations, or invert order books, downstream trading algorithms price assets on corrupt data.

MDRAP sits directly upstream of trading strategies, providing continuous, non-intrusive assurance:
- **Zero Order Execution**: Contains zero trading logic, zero private API secrets, and zero execution endpoints.
- **15-Rule Deterministic Microstructure Engine**: Evaluates spread crossings, sequence jumps, stale ticks, and clock skew in microsecond time.
- **Cryptographic Provenance**: Stamps every tick with a SHA-256 payload digest and end-to-end lineage hash.
- **Durable Local Storage**: Records incident time corridors into SQLite Write-Ahead Logging (WAL) for instant post-crash recovery.
- **Deterministic Replay**: Reconstructs market microstructure tick-by-tick under an isolated VirtualClock, guaranteeing bit-for-bit reproducible post-mortems.
- **RFC 6962 Merkle Trees**: Anchors audit batches into tamper-evident trees verified by external auditors using standard Python with zero dependencies.

---

## The Strategic Problem

| Operational Risk | What Typically Happens | How MDRAP Protects The Desk |
| :--- | :--- | :--- |
| **Burst Packet Drops** | WebSocket sequence jumps 200 packets during a flash crash; strategy misprices illiquid options. | Detects sequence gap instantly, quarantines downstream pricing, alerts risk desk. |
| **Crossed / Locked Books** | Venue cancellation is delayed 150ms; bid crosses ask; bot executes into phantom liquidity. | InvertedSpread rule fires in $<2\mu s$, isolating anomalous quotes from execution routing. |
| **Post-Loss Trade Disputes** | Firm disputes a bad fill with an exchange; neither side can prove what wire bytes arrived. | MDRAP exports RFC 6962 Merkle evidence proving exact wire payload, timestamp, and lineage. |
| **Credential Exposure Risk** | Trading API keys stored in third-party monitoring tools get compromised. | Impossible: MDRAP is strictly read-only and never accepts or stores trading credentials. |

---

## Deployment & Integration

MDRAP is completely self-hosted and runs on-premises or within a private cloud VPC:
- **Low Footprint**: $<35\text{ MB}$ RSS in pure-Python mode; $<16\text{ MB}$ in native C mode.
- **Throughput**: Validated at $350,000+$ ticks/second per core with native C vectorization ($32,800+$ in pure-Python).
- **Zero External Brokers**: No Kafka, Zookeeper, or Redis required. Operates as an embedded sidecar.
- **Fast Integration**: Connects to public Coinbase, Kraken, Binance, and Databento feeds in under 15 minutes.
