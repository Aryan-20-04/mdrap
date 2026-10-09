# MDRAP Phase 6 — Partition Ownership & Event Routing Specification

## 1. Executive Summary & Design Model
In a horizontally scaled market data platform, clear state ownership is critical. If two instances simultaneously attempt to process or persist the same instrument, sequence order is destroyed and split-brain corruption occurs.

MDRAP Phase 6 enforces **Strict Single-Shard Ownership per Symbol Universe**, ensuring that every market event has exactly one authoritative owner from ingestion to persistence.

---

## 2. Partition Ownership Matrix

| Resource Domain | Ownership Scope | Ownership Assignment | Conflict Prevention Mechanism |
| :--- | :--- | :--- | :--- |
| **Instrument Symbol** | Exclusive to 1 Shard | Normalized ticker symbol | Deterministic `SymbolPartitioner` lookup |
| **Monotonic Sequence** | Scoped to Shard ID | Autonomous integer counter | Local atomic sequence generator |
| **IngestLog WAL** | Dedicated `.seg` files | Shard-specific directory | Local exclusive OS file lock (`shard.lock`) |
| **Order Book State** | In-Memory Depth Book | Shard worker thread | 100% memory isolation; zero cross-shard locks |
| **Consumer Distribution**| Dedicated TCP SBE Port | Port 9002 (S0), 9003 (S1) | Port binding exclusivity |

---

## 3. Consumer Shard Discovery

Downstream trading consumers discover partition assignments via two mechanisms:
1. **Static Configuration Manifest**: Desk deployment manifests declare fixed ports (e.g., algorithmic desks trading tech symbols connect to Port 9002 for `AAPL/GOOGL`, Port 9003 for `MSFT/NVDA`).
2. **Dynamic Fleet Discovery (`/health/fleet`)**: Consumers query the fleet coordinator endpoint to retrieve active shard boundaries:
   ```json
   {
     "shard_0": {"symbols": "A-L", "sbe_port": 9002},
     "shard_1": {"symbols": "M-Z", "sbe_port": 9003}
   }
   ```
