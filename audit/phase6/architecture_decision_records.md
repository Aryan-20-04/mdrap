# MDRAP Phase 6 — Architecture Decision Records (ADRs)

## ADR-01: Adoption of Independent Symbol-Partitioned Shards over Distributed Middleware
- **Status**: **ACCEPTED**
- **Context**: As throughput scales beyond 3,000 eps toward 10,000+ eps and symbols scale from 20 to 500+, the single-process Python GIL and SQLite single-writer lock create contention. Commercial systems often reach for Kafka, RabbitMQ, or distributed databases.
- **Decision**: Deploy multiple independent MDRAP shard instances partitioned by symbol range or consistent hash, each with isolated WAL storage and distribution ports.
- **Consequences**: Zero external software dependencies, linear capacity scaling, sub-millisecond tail latency preserved, and failure blast radius isolated per shard. Requires consumers to connect to specific shard ports or via a lightweight routing proxy.

---

## ADR-02: Per-Partition Monotonic Sequencing over Global Total Order
- **Status**: **ACCEPTED**
- **Context**: In a partitioned architecture, enforcing a single global monotonic sequence number across all shards requires a centralized atomic sequence coordinator, which re-introduces a single point of failure and synchronization latency.
- **Decision**: Scope sequence numbering to `(shard_id, sequence_number)`. Each shard manages an independent, strictly monotonic 64-bit integer stream.
- **Consequences**: Shards proceed at full wire speed with zero cross-shard coordination. Financial semantics are fully preserved because order books for distinct symbols are causally independent.

---

## ADR-03: Decoupled Non-Blocking Fan-Out with Bounded Per-Client Queues
- **Status**: **ACCEPTED**
- **Context**: A synchronous broadcast loop over consumer TCP sockets experiences head-of-line blocking when any single client lags or experiences TCP buffer saturation.
- **Decision**: Allocate a dedicated, bounded queue (`maxsize=10000`) per connected consumer thread. If a client queue fills, the slow consumer is disconnected, protecting upstream ingestion and peer consumers.
- **Consequences**: Noisy-neighbor degradation is mathematically prevented. Slow consumers must reconnect and recover gaps from IngestLog replay.

---

## ADR-04: Static EOD Rebalancing over Dynamic Run-Time Shard Migration
- **Status**: **ACCEPTED**
- **Context**: Dynamic live partition rebalancing (similar to Kafka consumer group rebalancing) causes pipeline pauses, temporary split-brain states, and sequence reordering risks during active market hours.
- **Decision**: Enforce static partition configurations during trading sessions. Shard universe rebalancing is permitted strictly during scheduled EOD maintenance windows.
- **Consequences**: Zero runtime rebalancing jitter during market open; simplified operational failure model.

---

## ADR-05: Standard Library Fleet Monitoring over External Coordination Daemons
- **Status**: **ACCEPTED**
- **Context**: Monitoring multiple running shards could prompt the introduction of Consul, ZooKeeper, or etcd clusters.
- **Decision**: Implement a lightweight, zero-dependency `FleetHealthMonitor` utilizing standard HTTP/socket health scraping and local status aggregation.
- **Consequences**: Completely self-contained deployment, zero runtime cluster maintenance overhead, and full alignment with `/ponytail` simplicity.
