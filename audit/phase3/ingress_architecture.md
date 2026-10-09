# MDRAP Phase 3 — Ingress Architecture Specification

**Document Identifier**: `MDRAP-INGRESS-P3-001`  
**Date**: October 9, 2026  
**Status**: APPROVED & IMPLEMENTED  

---

## 1. Architectural Model

MDRAP separates raw feed ingress from canonical event processing via an explicit 6-stage lifecycle pipeline:

```
[External Venue]
       │
       ▼ (Socket / Multicast / IPC / Replay)
1. RAW_RECEIVED   ───► Frame length validation, ingress receive timestamping
       │
       ▼
2. PARSED         ───► Extraction of wire fields (JSON / SBE / ITCH)
       │
       ▼
3. VALIDATED      ───► Sequence domain audit (gap & duplicate detection)
       │
       ▼
4. NORMALIZED     ───► Translation into typed CanonicalEvent with venue identity
       │
       ▼
5. ACCEPTED       ───► Quality engine rules evaluation (price sanity, crossed quotes)
       │
       ▼
6. COMMITTED      ───► IngestLog WAL atomic fsync and SQLite projection update
```

---

## 2. Adapter State Machine

Each feed adapter operates as a deterministic finite-state automaton:
- `UNINITIALIZED`: Configuration validated, no sockets or transport allocations.
- `CONNECTING`: Establishing transport session, DNS resolution, TLS handshake.
- `CONNECTED`: Session established; subscriptions ready.
- `STREAMING`: Actively polling and dispatching raw market data frames.
- `RECONNECTING`: Handling network interruption with bounded exponential backoff.
- `ERROR`: Fatal framing violation or configuration rejection.
- `CLOSED`: Graceful shutdown; all socket handles and buffers drained and released.

---

## 3. Working Implementations

- **`BaseFeedAdapter`** (`src/mdrap/ingress.py`): Reusable base class handling lifecycle transitions, telemetry accounting, and sequence gap audits.
- **`ReplayFeedAdapter`** (`src/mdrap/ingress.py`): Deterministic replay adapter used in continuous integration and quantitative simulation.
