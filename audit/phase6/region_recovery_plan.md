# MDRAP Phase 6 — Regional Disaster Recovery & Promotion Plan

## 1. Executive Summary & Objectives
This plan governs the procedural failover and recovery actions required in the catastrophic event of total primary site failure (e.g., severe telecommunications severance or datacenter power loss at Equinix NY4).

- **Target Recovery Time Objective (RTO)**: **< 5.0 Minutes**
- **Target Recovery Point Objective (RPO)**: **< 60.0 Seconds** (asynchronous segment shipping lag)

---

## 2. Disaster Recovery Failover Workflow

```
[ Primary Datacenter Outage Declared ] ──> [ Execute BGP / DNS Route Swing ]
                                                          │
                                                          ▼
[ Normal Trading Resumed at DR Site ] <── [ Promote Standby Shards (cli.py dr promote) ]
```

### Step 1: Primary Fencing & Traffic Redirection
1. Network engineering engages DNS / BGP Anycast routing swing to direct feed traffic to Chicago ingress routers.
2. Invalidate primary site publishing credentials to prevent split-brain if primary partially recovers.

### Step 2: Standby Data Verification & Promotion
1. Verify integrity of the latest mirrored WAL segments in `/var/data/mdrap_standby/`.
2. Execute automated promotion command:
   ```bash
   python cli.py dr promote --config /opt/mdrap/config/dr_standby.json
   ```
3. Shards replay uncheckpointed records, bind to local SBE distribution sockets, and emit readiness status.

### Step 3: Downstream Consumer Reconnection
1. Trading desks' client SDKs automatically failover to secondary DR IP addresses (`10.300.4.10:9002`).
2. Consumers re-authenticate and query sequence heads to resume algorithmic execution.
