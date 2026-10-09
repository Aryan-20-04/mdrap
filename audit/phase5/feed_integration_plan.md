# Phase 5 Feed Integration & Source Validation Plan

**Scope**: Upstream Exchange Ingress Adapters & Protocol Mapping  
**Date**: 2026-10-09  

---

## 1. Supported Ingress Protocols

MDRAP implements extensible feed adapters for institutional market data feeds:
1. **NASDAQ ITCH 5.0 / MoldUDP64**: Direct binary multicast protocol with MoldUDP64 framing.
2. **Polygon / Binance / Kraken**: WebSocket streaming adapters for L1 quotes and trades.
3. **Replay Adapter (`ReplayFeedAdapter`)**: Deterministic frame injection for reproducible testing and staging pilots.

---

## 2. Ingress Validation Requirements

Before connecting to any live exchange feed:
1. **Source Licensing Verification**: Ensure tenant possesses signed distributor agreements.
2. **Clock Source Identification**: Tag events with exchange timestamp or gateway receive timestamp (`clock_source="GATEWAY_RECV"` when exchange clock is absent).
3. **Monotonic Sequence Audit**: Enable `gap_detection_enabled=True` to audit upstream dropped UDP packets.
