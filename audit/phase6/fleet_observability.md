# MDRAP Phase 6 — Fleet-Wide Observability and Telemetry Architecture

## 1. Executive Summary & Design Model
When scaling from a single-node pilot to a multi-instance partitioned fleet, local process health checks are insufficient. Operations teams require unified visibility into aggregate throughput, individual shard queues, partition-level health, and cross-shard consumer lag without imposing monitoring overhead on the tick ingestion hot path.

MDRAP Phase 6 implements **Fleet-Wide Telemetry with Bounded Cardinality**, scraping shard health signals via asynchronous telemetry endpoints and aggregating them into a unified operational dashboard.

---

## 2. Unified Instance Identity Specification

Every deployed instance or shard exports a standardized, immutable identity tuple:

```json
{
  "environment": "production-us-east",
  "deployment_id": "mdrap-prod-cluster-01",
  "shard_id": 0,
  "shard_name": "Shard_A_L",
  "version": "1.0.0-phase6",
  "config_revision": "rev-462da61-01"
}
```

### Strict Label Cardinality Rule
In accordance with Prometheus best practices, metric labels are strictly restricted to bounded, enumerable sets:
- **Permitted Labels**: `environment`, `shard_id`, `venue`, `status`, `client_id`.
- **Prohibited Labels**: Individual `event_id`, high-cardinality `symbol` (e.g. 50,000 options strikes), or individual timestamps. High-cardinality dimensions are tracked exclusively via local counters or sampled tracing.

---

## 3. Fleet Health Telemetry Schema

The `FleetCoordinator` (`src/partition.py`) exposes an aggregate telemetry payload:

```json
{
  "fleet_status": "HEALTHY",
  "total_shards": 2,
  "aggregate_events_processed": 20000,
  "aggregate_queue_backlog": 0,
  "aggregate_consumers": 2,
  "shards": {
    "shard_0": {
      "name": "Shard_A_L",
      "is_running": true,
      "sequence_head": 10000,
      "total_processed": 10000,
      "queue_depth": 0,
      "active_consumers": 1,
      "evictions": 0
    },
    "shard_1": {
      "name": "Shard_M_Z",
      "is_running": true,
      "sequence_head": 10000,
      "total_processed": 10000,
      "queue_depth": 0,
      "active_consumers": 1,
      "evictions": 0
    }
  }
}
```

---

## 4. Decoupled Scraping Invariant

- **Zero Ingestion Interference**: Scraping `/health/fleet` or `/metrics` executes on dedicated asynchronous I/O threads. Even if the Prometheus scraper times out or disconnects mid-scrape, the core shard ingestion loop is completely insulated from network socket pauses.
- **Fail-Safe Aggregation**: If Shard 0 is healthy but Shard 1 is paused, the aggregate status immediately transitions to `DEGRADED`. A fleet-level green status is mathematically barred from masking a crippled shard.
