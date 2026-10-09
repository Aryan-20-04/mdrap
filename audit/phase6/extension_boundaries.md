# MDRAP Phase 6 — Architectural Extension Boundaries & Core Scope

## 1. Executive Summary & Design Principle
A classic failure mode of financial infrastructure is "scope creep"—allowing peripheral analytics, trading strategies, and visualization dashboards to contaminate the low-latency market data core.

In strict accordance with the `/ponytail` principle (YAGNI, minimal clean core, zero bloat), MDRAP Phase 6 enforces an explicit **Extension Boundary Model**.

---

## 2. Platform Layer Delineation

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ LAYER 1: NATIVE HOT-PATH KERNEL (C / Fastpath)                              │
│  - Simple Binary Encoding (SBE) packing/unpacking                           │
│  - Lock-free SPSC seqlock shared memory ring buffers                        │
│  - Microsecond price bounds and numeric validations                         │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ LAYER 2: CORE MARKET DATA ENGINE (Python stdlib Orchestration)               │
│  - Gateway normalization and clock source attribution                       │
│  - Multi-rule quality validation and quarantine storage                     │
│  - IngestLog WAL durability boundary with group fsync                       │
│  - Cross-feed multi-source reconciler and provenance tracking               │
│  - Decoupled bounded consumer fan-out and tenant quota enforcement          │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ LAYER 3: INGRESS ADAPTERS (Modular Plug-ins)                                │
│  - NASDAQ ITCH 5.0, BATS Pitch, Polygon.io, WebSocket venue adapters         │
│  - Standalone parsers mapping venue wire frames into CanonicalEvent         │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ LAYER 4: DOWNSTREAM CONSUMER SERVICES (EXPLICITLY OUT-OF-CORE)              │
│  - Algorithmic trading strategies (Avellaneda-Stoikov, market makers)       │
│  - Transaction Cost Analysis (TCA) and broker scorecards                    │
│  - Terminal displays, web charts, and vessel AIS tracking                   │
│  - *Action*: Deprecated in core; moved to standalone consumer processes.    │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Core Engine Invariants

1. **Zero Strategy Logic in Core**: The core engine must never contain trading decision logic, order generation, or portfolio optimization. It is strictly market data infrastructure.
2. **Decoupled Packaging**: Downstream consumers ingest data via public SBE or SHM interfaces. No strategy code may import internal private engine threads.
