# MDRAP Phase 6 — Fleet Operations & Multi-Instance Administration

## 1. Executive Summary & Operational Scope
This runbook provides platform operators with the tools and procedures to observe, manage, drain, and troubleshoot multi-shard MDRAP deployments without causing pipeline disruptions.

---

## 2. Core Operational Observability Answers

| Operational Question | Telemetry Source | Inspection Method |
| :--- | :--- | :--- |
| **Which instances are running?** | `FleetCoordinator.fleet_health()` | `python cli.py fleet status` |
| **What partitions does each own?** | `SymbolPartitioner.shard_ranges` | Range: `A-L` (Shard 0), `M-Z` (Shard 1) |
| **Is any shard degraded?** | Aggregate `fleet_status` | Returns `HEALTHY` or `DEGRADED` |
| **How deep are the queues?** | `in_queue.qsize()` | Prometheus `mdrap_shard_queue_depth` |
| **Are consumers keeping up?** | `session.stream_queue.qsize()` | Prometheus `mdrap_consumer_queue_depth` |
| **Have any consumers been evicted?**| `_total_evictions` counter | Prometheus `mdrap_consumer_evictions_total` |

---

## 3. Standard Operational Procedures

### 3.1 Draining a Shard for Host Maintenance
```bash
# Gracefully drain Shard 0 without impacting Shard 1
python cli.py fleet drain --shard 0 --timeout 5.0
```

### 3.2 Forcibly Disconnecting an Abusive Consumer
```bash
# Evict consumer across all shards immediately
python cli.py fleet evict-consumer --consumer-id DESK_ALPHA --reason "Backpressure breach"
```
