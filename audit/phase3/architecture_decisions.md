# MDRAP Phase 3 — Architecture Decisions Record (ADR)

**Document Identifier**: `MDRAP-ADR-P3-001`  
**Date**: October 9, 2026  
**Status**: APPROVED  

---

## 1. Resolution of the 18 Architectural Questions

### Q1: What is the canonical public event representation?
- **Decision**: A typed, immutable structure (`CanonicalEvent`) containing:
  - `event_id`: monotonic 64-bit integer formatted as string or uint64.
  - `instrument_id`: normalized symbol string (e.g. `AAPL`, `BTC-USDT`).
  - `event_type`: enum string (`TRADE`, `BBO`, `BOOK_UPDATE`, `HEARTBEAT`).
  - `source`: venue identifier (e.g. `NASDAQ`, `BINANCE`, `KRAKEN`).
  - `feed_id`: feed channel identifier (e.g. `ITCH_DIRECT`, `DEPTH5`).
  - `exchange_ts`: nanoseconds since UNIX epoch (int64) or float seconds.
  - `receive_ts`: nanoseconds since UNIX epoch (int64).
  - `sequence`: uint64 source sequence number.
  - `price`: double precision float or int64 fixed-point (8 decimal places).
  - `size`: double precision float or int64 fixed-point (4 decimal places).
  - `bid` / `ask`: optional double precision quotes.
  - `quality_flag`: int (VALID=0, SUSPICIOUS=1, INVALID=2).
- **Alternatives**: Pure JSON dictionary vs binary C struct.
- **Rationale**: Python uses `CanonicalEvent` dataclass; C++/Rust/Java use direct binary memory layouts matching SBE/C struct for zero-copy performance.

### Q2: What are the canonical sequence-number and event-identity semantics?
- **Decision**: Three distinct sequence domains:
  1. `source_sequence`: Sequence assigned by external venue (scope: `source + feed_id + session_id`).
  2. `ingest_sequence`: Monotonic sequence assigned by MDRAP IngestLog WAL on append (scope: local node instance).
  3. `canonical_sequence`: Global monotonic order assigned by reconciler/engine.
- **Rationale**: Avoids conflating external feed drops with internal engine sequencing.

### Q3: What is the ownership model for event memory and buffers?
- **Decision**:
  - Ingestion: Bounded ring buffer owned by IngestLog / SHM publisher.
  - Native Consumers (C++): Borrowed const view during callback/poll; consumer copies if retaining beyond dispatch cycle.
  - Rust / Java Consumers: Safe borrowed slice or value copy depending on transport (SHM vs Socket).

### Q4: Which APIs are stable public contracts and which are internal?
- **Stable Public Contracts**:
  - REST & WebSocket endpoints (`/api/v1/stream`, `/api/v1/events`, `/liveness`, `/readiness`, `/health`).
  - Native Consumer C ABI (`mdrap_consumer_t`) and headers (`sdk/cpp/include/mdrap/consumer.hpp`).
  - `FeedAdapter` abstract base class (`src/mdrap/ingress.py`).
  - Configuration schema (`mdrap.toml`).
- **Internal APIs**:
  - `fastpath.c` internal SIMD routines, `BinaryJournal` legacy shims, direct SQLite table schema definitions.

### Q5: What is the supported native ABI and platform matrix?
- **Decision**: C99 / C++17 ABI on Linux (x86-64, aarch64) and Windows (x86-64).
- **Standard**: POSIX Shared Memory on Linux (`shm_open`), Win32 Named Shared Memory on Windows (`CreateFileMappingA`).

### Q6: What is the source-adapter interface?
- **Decision**: `FeedAdapter` base class defining:
  - `connect()`, `disconnect()`, `subscribe(symbol)`, `poll()`, `stats()`.
  - Emits `RawEvent` with raw bytes, source timestamp, and receive timestamp.

### Q7: What is the normalized market-data model?
- **Decision**: Canonical quotes and trades with validated bids/asks, non-negative quantities, strict monotonic timestamps, and quality status tags.

### Q8: How are source identity, feed identity, session identity, and sequence domains represented?
- **Decision**: Explicit tuple `(venue, feed_id, session_id)` in raw events and metadata; distinct `source_sequence` vs `ingest_seq`.

### Q9: What is the authoritative persistence and recovery boundary?
- **Decision**: `IngestLog` WAL with CRC32 checksums and atomic fsync. Any event not written to the WAL is not committed. SQLite projections are asynchronously derived and replayable.

### Q10: What consistency model does replication use?
- **Decision**: Semi-synchronous active-passive replication. Primary persists to local WAL, streams WAL frame bytes to passive standby over TCP; standby acks offset before client commits are finalized in HA mode.

### Q11: What is the failover authority and fencing mechanism?
- **Decision**: Monotonic Epoch Tokens (`epoch: int`). Standby promotion increments `epoch` via distributed coordinator or manual administrative trigger. Old primary with lower epoch is fenced immediately on attempted append (`StaleEpochError`).

### Q12: Which node is allowed to publish authoritative data?
- **Decision**: Exactly one node — the node holding the highest confirmed `epoch` with an active lease.

### Q13: How are consumers informed about gaps, epochs, resets, and recovery?
- **Decision**: Control frame emitted across streams: `EPOCH_TRANSITION`, `SEQUENCE_GAP_DETECTED`, `REPLAY_ACTIVE`, `STREAM_RESUMED`.

### Q14: What event is counted for licensing purposes?
- **Decision**: Configurable billing unit:
  - Default: `DISTRIBUTED_EVENT` (each normalized event successfully dispatched to an authorized client).
  - Alternative: `INGESTED_EVENT` (each valid event ingested).

### Q15: How are entitlements evaluated and revoked?
- **Decision**: API key check against `ClientEntitlement` cached in memory; revocations immediately invalidate client sessions and drop stream dispatch within 1 cycle.

### Q16: What audit records must be retained?
- **Decision**:
  1. All WAL frames (cryptographic SHA-256 Merkle chain in IngestLog).
  2. Hourly usage accounting rollups in SQLite (`metering_records`).
  3. Administrative entitlement changes and revocations.

### Q17: Which features are production-supported, experimental, deprecated, or out of scope?
- **Production-Supported**: Core Ingestion, IngestLog WAL, Quality Engine, Runtime Lifecycle, Supervised Workers, Bounded Backpressure, REST/WS API, C++ & Java Consumer SDKs, Replay Feed Adapter, Metering.
- **Beta / Test**: Rust Consumer SDK (due to host cargo toolchain limitation), Active-Passive Replication.
- **Experimental**: Vessel, Options, News, Risk models.
- **Deprecated**: `BinaryJournal`, raw `Pipeline` class.
- **Out of Scope**: Multi-cloud Paxos consensus, kernel-bypass NIC drivers on Windows.

### Q18: Which features require external exchange access or special hardware?
- **Decision**: Kernel bypass (requires AF_XDP/DPDK NIC & Linux kernel 5.4+); Live NASDAQ ITCH multicast (requires ITCH direct cross-connect). Both provide synthetic deterministic test harnesses.
