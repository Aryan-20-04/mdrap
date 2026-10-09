# MDRAP Phase 9 — UAT Deployment Architecture (Mode A)

## 1. UAT Topology Overview
In Mode A (Local Multi-Process Integration), MDRAP is deployed across isolated OS processes communicating over loopback TCP sockets and independent storage directories:

```mermaid
flowchart TD
    subgraph UAT_Environment ["MDRAP Mode A UAT Isolated Environment"]
        subgraph Node_Primary ["Primary Node (PID 1)"]
            pri_daemon["MarketDataDaemon (Port 61517)"]
            pri_wal["Primary IngestLog WAL (temp/_uat_pri.db)"]
            pri_fanout["AsyncFanoutManager (Primary Egress)"]
        end

        subgraph Node_Secondary ["Secondary Standby Node (PID 2)"]
            sec_daemon["MarketDataDaemon (Port 61518)"]
            sec_wal["Secondary IngestLog WAL (temp/_uat_sec.db)"]
            sec_fanout["AsyncFanoutManager (Standby Egress)"]
        end

        subgraph Coordinator ["Quorum & Lease Coordinator"]
            consensus["ConsensusCoordinator (Epoch Leasing)"]
        end

        subgraph Clients ["UAT Consumer Fleet"]
            c1["StreamClient 1 (HFT Algo)"]
            c2["StreamClient 2 (Risk Monitor)"]
            cN["StreamClient N (Analytics)"]
        end
    end

    consensus -->|Epoch Lease Token| pri_daemon
    consensus -.->|Standby Heartbeat| sec_daemon
    pri_daemon --> pri_wal
    pri_daemon --> pri_fanout
    sec_daemon --> sec_wal
    sec_daemon --> sec_fanout
    pri_fanout --> c1
    pri_fanout --> c2
    pri_fanout --> cN
```

## 2. Process & Failure Domain Isolation
1. **Network Ports**: Primary and Secondary daemons bind to distinct dynamic ephemeral ports (`61517` vs `61518`).
2. **Storage Paths**: Each daemon writes to an independent SQLite WAL database and file path (`_uat_pri.db` vs `_uat_sec.db`).
3. **Environment Secrets**: API key salt (`MDRAP_API_KEY_SALT`) and daemon admin token (`MDRAP_DAEMON_TOKEN`) are loaded from environment variables and never hardcoded in code.
4. **Failure Independence Limitation**: Both processes share the host CPU and OS kernel. True physical host isolation (Mode B) is unavailable and explicitly documented.
