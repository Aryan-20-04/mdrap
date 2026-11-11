# Incident Lifecycle & Anomaly Management

MDRAP groups individual anomalous market data events into structured, auditable **Data Quality Incidents** (`mdrap.incidents`).

---

## 1. Incident Lifecycle

```
[Normal Ingestion]
       |
       v
[Anomaly Detected] (e.g., Sequence Gap, Stale Tick, Inverted Book)
       |
       +---> [Check Active Incidents]
                 |
                 +---> Match found for (Venue, Instrument, AnomalyType)?
                 |         |
                 |         +---> YES: Extend active incident (update end_timestamp, append event_id)
                 |         |
                 |         +---> NO:  Create new DataQualityIncident
                 |                    - Generate monotonic incident ID (inc-<ts>-<seq>)
                 |                    - Classify severity (LOW, MEDIUM, HIGH, CRITICAL)
                 |                    - Open forensic time corridor (start_ts = event_ts - buffer)
                 |
                 v
       [Quarantine Trigger Event]
                 |
                 v
       [Persist Incident to SQLite WAL]
                 |
                 v
       [Anchor Batch to RFC 6962 Merkle Tree]
                 |
                 v
       [Corridor Closes after Quiet Window]
                 |
                 v
       [Incident Ready for Forensic Replay & Post-Mortem Export]
```

---

## 2. Severity Classification

| Severity | Criteria | Example | Downstream Impact |
| :--- | :--- | :--- | :--- |
| **LOW** | Minor clock skew ($<100\text{ms}$), transient single stale tick | Binance BTC tick delayed 60ms | Logged; telemetry updated |
| **MEDIUM** | Sequence gap $< 10$ packets, non-crossing spread widening | Kraken ETH packet jump $101 \to 105$ | Quarantined; strategy warned |
| **HIGH** | Large sequence gap ($> 50$ packets), crossed book ($> 10\text{ bps}$) | Binance BTC book inverted for 200ms | Pricing halted for venue |
| **CRITICAL**| Multiple venue disconnections, severe structural corruption | Feed dropped during flash crash | Complete failover initiated |

---

## 3. The `DataQualityIncident` Model

```python
@dataclass(slots=True)
class DataQualityIncident:
    incident_id: str                   # e.g., "inc-1700000001-0001"
    provider: str                      # Venue (e.g., "BINANCE", "COINBASE")
    instrument: str                    # Instrument (e.g., "BTC-USD")
    anomaly_type: str                  # "SEQUENCE_GAP", "FEED_STALE", "CLOCK_SKEW", etc.
    severity: str                      # "LOW", "MEDIUM", "HIGH", "CRITICAL"
    start_timestamp: float             # Incident start time (Unix epoch)
    end_timestamp: Optional[float]     # Incident end time (Unix epoch)
    event_ids: list[str]               # Associated CanonicalEvent identifiers
    impact_summary: str                # Human-readable impact description
    is_active: bool = True             # True while ongoing, False when closed
    reproduced: bool = False           # Set to True after successful deterministic replay
    reproduction_hash: Optional[str] = None # SHA-256 state hash from replay verification
```

---

## 4. Querying Incidents

### Via CLI:
```bash
# List all recorded incidents
python -m mdrap.cli incident list

# Inspect detailed incident record
python -m mdrap.cli incident show inc-1700000001-0001
```

### Via Python API:
```python
from mdrap.incidents import IncidentManager

inc_mgr = IncidentManager(db_path="data/mdrap.db")
incidents = inc_mgr.query_incidents(provider="COINBASE", severity="HIGH")

for inc in incidents:
    print(f"[{inc.severity}] {inc.incident_id}: {inc.anomaly_type} ({inc.impact_summary})")
```

---

## 5. Corridor Isolation & Replay Readiness

When an incident is opened, MDRAP preserves the full surrounding event window (typically 5 to 15 seconds before the anomaly to 5 to 15 seconds after). This corridor contains all baseline and anomalous ticks necessary for deterministic reconstruction using `IncidentReplayEngine`.
