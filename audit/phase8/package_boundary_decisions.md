# MDRAP Phase 8 — Package Boundary Decisions

## 1. Context & Motivation
Historical MDRAP code grew to incorporate derivatives pricing, broker performance analytics, execution strategies, and maritime supply chain intelligence. In Phase 8, architecture governance requires formal definition of what constitutes "Core Engine" versus "Companion Package".

## 2. Decision Log

### Decision 1: Core Engine Scope Definition (`mdrap-core`)
- **Included in Core**:
  - Feed ingestion & frame normalization (`gateway.py`, `models.py`, `protocol.py`).
  - Data quality evaluation & anomaly detection (`quality.py`, `fastpath.c`, `rules.def`).
  - Multi-venue reconciliation & NBBO book building (`reconciliation.py`, `bbo.py`, `depth.py`).
  - Low-latency IPC & network streaming (`shm.py`, `service.py`, `partition.py`).
  - Authoritative persistence & historical auditing (`ingestlog.py`, `storage.py`, `historical_verifier.py`).
- **Rationale**: These components are essential to convert raw, noisy feeds into validated, durable, canonical streams.

### Decision 2: Extraction of Options & Derivatives (`mdrap-options`)
- **Decision**: Move derivatives pricing models (`options.py`) to companion package `mdrap-options`.
- **Rationale**: Options pricing (BSM, Greeks, Binomial trees) is a downstream valuation consumer of market data, not part of market data normalization or validation.

### Decision 3: Extraction of Transaction Cost Analysis (`mdrap-analytics`)
- **Decision**: Move TCA execution benchmarking (`tca.py`) to companion package `mdrap-analytics`.
- **Rationale**: Post-trade TCA evaluates client routing quality against historical NBBO; it does not process live feeds on the critical path.

### Decision 4: Extraction of Strategy & EMS SDK (`mdrap-strategies`)
- **Decision**: Move Avellaneda-Stoikov market maker and Strategy SDK (`strategy_sdk.py`) to `mdrap-strategies`.
- **Rationale**: Algorithmic trading strategies consume canonical data; embedding trading algorithms in the market data engine creates tight coupling and conflicts of interest.

### Decision 5: Extraction of Vessel Intelligence (`mdrap-contrib-vessel`)
- **Decision**: Move AIS vessel tracking (`vessel.py`) to `mdrap-contrib-vessel`.
- **Rationale**: Alternative maritime data is orthogonal to financial market data infrastructure.

## 3. Governance & Quality Gates
Each companion package is subject to the same strict coding standards as core:
- Stdlib-first architecture.
- 100% test pass rate.
- Deterministic behavior with fixed seeds.
