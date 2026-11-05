# MDRAP v5.3 — Systems Architecture & Dataflow Map

**Audit Classification:** SOURCE-VERIFIED & ARCHITECTURALLY MAPPED  
**Date:** 2026-11-05  
**Auditor:** Principal Low-Latency Systems Architect  
**Target Repository:** `c:\Users\KIIT0001\Desktop\Projects\mdrap` (Branch: `v5.3-engineering-audit`)  

---

## 1. Architectural Tiers

MDRAP is designed as an institutional sidecar sitting between noisy external market data feeds and downstream execution algorithms, analytics engines, and regulatory databases.

```mermaid
flowchart TB
    subgraph Tier1 [Tier 1: Ingress & Normalization]
        WS[WebSocket / Binary Ingress] --> Norm[normalize()]
        Norm --> SBE_Dec[SBE Frame Decoder]
        Norm --> ITCH_Dec[ITCH 5.0 Parser]
    end

    subgraph Tier2 [Tier 2: Quality Engine & Fastpath Kernel]
        FastpathC["Native C Kernel (fastpath.c)"]
        FastpathPy["Pure Python Fallback (quality.py)"]
        Welford["Rolling Welford Corridor"]
        Dedup["2-Gen Sliding Dedup Table"]
    end

    subgraph Tier3 [Tier 3: Stateful Engine & Transaction Journal]
        EngineStep["Engine.step()"]
        UndoLog["Undo Journal (Transaction Rollback)"]
        Recon["Cross-Feed Reconciliation"]
        BBO["BBO Synthesis"]
    end

    subgraph Tier4 [Tier 4: Lock-Free IPC Distribution]
        SHMWrite["SHMWriter (Two-Phase Commit)"]
        RingBuf[("SHM v3 Ring Buffer (64B Cacheline Aligned)")]
        SHMRead["SHMReader (Lock-Free Seqlock)"]
    end

    subgraph Tier5 [Tier 5: Execution & Microstructure Simulation]
        Matching["RealisticMatchingEngine"]
        Queue["Price-Time Priority Deques"]
        Risk["14 Pre-Trade Risk Gates"]
        Strat["Quantitative Strategies (AS-MM)"]
    end

    subgraph Tier6 [Tier 6: Persistence & Recovery]
        WAL["IngestLog (Binary Segmented WAL)"]
        CRC["CRC32C Frame Checksumming"]
        Snap["Snapshot Engine"]
        SQLite["SQLite Projections (WAL Mode)"]
    end

    Tier1 --> Tier2
    Tier2 --> Tier3
    Tier3 --> Tier4
    Tier3 --> Tier6
    Tier4 --> Tier5
```

---

## 2. Memory Geometry & Cache Alignment

To eliminate CPU L1 data cache misses and multi-core false sharing:
1. **Header Cache Line 1 (64 bytes):** Magic (`0x4D44525033000000`), Version (`3`), Slot Size (`128`), Slot Count ($2^k$), Epoch ID (`uint64`), Head Sequence (`uint64`), padded to 64 bytes.
2. **Header Cache Line 2 (64 bytes):** Heartbeat Timestamp (`float64`), Dropped Ticks (`uint64`), Flags (`uint32`), padded to 64 bytes.
3. **Slot Memory Layout (128 bytes = exactly 2 cache lines):**
   - Offset +0: `commit_seq` (`uint64`, seqlock commit marker)
   - Offset +8: Event Type (`uint8`), Status (`uint8`), Crossed Flag (`uint8`), Presence Mask (`uint8`)
   - Offset +16: Exchange Timestamp (`float64`), Ingest Timestamp (`float64`)
   - Offset +32: Price (`float64`), Quantity (`float64`)
   - Offset +48: Bid Price (`float64`), Ask Price (`float64`)
   - Offset +64: Bid Size (`float64`), Ask Size (`float64`)
   - Offset +80: Symbol (16 ASCII bytes)
   - Offset +96: Source (8 ASCII bytes)
   - Offset +104: Engine Processing Microseconds (`float32`), Reserved Padding (20 bytes).

---

## 3. Pre-Trade Risk Architecture (Tier 5)

Every order submitted by an algorithmic strategy traverses the 14 institutional risk gates defined in [`packages/mdrap-strategies/mdrap_strategies/risk_engine.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/packages/mdrap-strategies/mdrap_strategies/risk_engine.py):
1. `PRICE_COLLAR`: Max price divergence (+/- 150 bps) from prevailing mid.
2. `MAX_ORDER_QTY`: Per-order quantity ceiling (500 units).
3. `MAX_ORDER_NOTIONAL`: Per-order notional ceiling (\$250,000.00).
4. `MAX_POSITION_QTY`: Cumulative inventory ceiling (2,500 units).
5. `MAX_POSITION_NOTIONAL`: Cumulative inventory notional ceiling (\$1,000,000.00).
6. `MAX_DRAWDOWN`: Max portfolio drawdown threshold (5.0%).
7. `ORDER_RATE_LIMIT`: Max orders per rolling second (100 orders/sec).
8. `SHORT_SELL_RESTRICTION`: Regulatory SSR / tick rule check.
9. `FAT_FINGER_FILTER`: Size/price sanity check vs 30-day ADV.
10. `VENUE_CAP`: Gross exposure limit per destination venue (\$750,000.00).
11. `SELF_TRADE_PREVENTION`: Wash trade matching prevention.
12. `RESTRICTED_LIST`: Hard block on restricted/halted symbols.
13. `MIN_NOTIONAL`: Exchange minimum trade size requirement.
14. `MAX_OPEN_ORDERS`: Concurrent resting order count ceiling (50 orders).
