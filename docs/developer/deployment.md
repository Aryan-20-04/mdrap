# Sidecar Deployment & Production Topologies

MDRAP is designed to be deployed as a local **reliability sidecar** sitting between market data providers and algorithmic trading containers.

---

## 1. Sidecar Pattern Architecture

In an institutional trading environment, trading algorithms should focus exclusively on risk and alpha generation, while MDRAP guarantees that the inputs they consume are clean, validated, and provably untampered.

```
                   +---------------------------+
                   |  Public Exchange Feeds    |
                   +---------------------------+
                                 | (Raw WebSocket / Multicast)
                                 v
+-------------------------------------------------------------+
| HOST / POD BOUNDARY                                         |
|                                                             |
|   +-----------------------+     +-----------------------+   |
|   |  MDRAP Sidecar        |     |  Trading Strategy     |   |
|   |  - Passive Tap        |     |  - Execution Engine   |   |
|   |  - 15-Rule Validation |===> |  - Alpha Logic        |   |
|   |  - Provenance & WAL   | SHM |  - Risk Gateways      |   |
|   |  - RFC 6962 Anchoring |     |                       |   |
|   +-----------------------+     +-----------------------+   |
|              |                                              |
|              v (SQLite WAL)                                 |
|   +-----------------------+                                 |
|   | Local NVMe Storage    |                                 |
|   +-----------------------+                                 |
+-------------------------------------------------------------+
```

---

## 2. Low-Latency Inter-Process Communication (IPC)

For co-located Linux and Windows hosts, MDRAP supports distribution of validated canonical events via **Shared Memory (SHM) Ring Buffers** (`mdrap.shm`):

- **Zero-Copy Readers**: Downstream trading algorithms consume validated ticks directly from a lockless ring buffer without network socket overhead.
- **Seqlock Consistency**: A 64-bit sequence counter prevents torn reads when writers update quotes concurrently.
- **Fail-Safe Decoupling**: If downstream readers slow down or crash, MDRAP's ring buffer advances cleanly without stalling feed ingestion.

---

## 3. High-Availability Topologies

For mission-critical production desks:

1. **Dual Independent Sidecars**: Deploy two MDRAP sidecar instances per venue (e.g. `sidecar-primary` and `sidecar-secondary`) connected to separate physical network interfaces.
2. **Local Disk WAL**: Each sidecar maintains its own local SQLite WAL database. Because SQLite writes locally, network partitions between data centers cannot stall market data ingestion.
3. **Out-of-Band Incident Forwarding**: When an anomaly is detected, MDRAP asynchronously pushes structured incident reports to internal risk monitoring systems (via webhooks or syslog).

---

## 4. Production Checklist

- [ ] Dedicated system user (`mdrap`, UID 10001) configured.
- [ ] Local NVMe volume mounted with `noatime` for SQLite WAL storage.
- [ ] Sizing verified: at least 2 vCPUs and 2 GB RAM per 10,000 ticks/sec.
- [ ] Security salt configured via `MDRAP_API_KEY_SALT`.
- [ ] API bound strictly to loopback (`127.0.0.1:8000`).
- [ ] Zero trading API secrets present in process environment.
