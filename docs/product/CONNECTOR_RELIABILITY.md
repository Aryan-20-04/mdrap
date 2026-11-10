# MDRAP Connector Reliability & Telemetry Specification

**Document Version**: 2.0  
**Responsible Agent**: Agent 2 (Market Data Ingestion & Provider Reliability)  
**Status**: VERIFIED IMPLEMENTATION  

---

## 1. Provider-by-Provider Connector Audit

MDRAP provides persistent, encrypted TLS WebSocket connectors across global cryptocurrency exchanges. Each exchange exhibits unique wire protocols, sequence number semantics, and clock behaviors:

| Provider | Wire Protocol | Timestamp Format | Sequence Support | Heartbeat Mechanism | Reconnection Strategy |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Binance** | `@depth5@100ms` / `@bookTicker` JSON | Microsecond integer (`E`) | Implicit in diff stream (`u`, `U`); absent in direct ticker | Client-side Ping (`ping_interval=20s`) | Exponential backoff (1s to 30s) |
| **Bybit** | `orderbook.5` / `tickers` v5 JSON | Millisecond integer (`ts`) | Monotonic integer (`seq` / `u`) | JSON ping (`{"op": "ping"}`) | Exponential backoff (1s to 30s) |
| **Kraken** | `spread` / `ticker` v1 JSON | Floating-point seconds | Absent in L1 public; present in L2 book | WebSocket protocol ping | Exponential backoff (1s to 30s) |
| **OKX** | `bbo-tbt` / `books5` v5 JSON | Millisecond integer (`ts`) | Monotonic integer (`seqId`) | JSON ping (`"ping"`) | Exponential backoff (1s to 30s) |
| **Coinbase** | `ticker` / `level2` JSON | ISO 8601 string (`time`) | Monotonic integer (`sequence`) | WebSocket protocol ping | Exponential backoff (1s to 30s) |

---

## 2. Standardized Connector Contract (`ProviderTelemetry`)

To prevent ambiguous health claims, MDRAP enforces an explicit telemetry interface across every provider:

```python
class ConnectorState(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    DEGRADED = "DEGRADED"      # Socket open, but experiencing sequence gaps or parsing errors
    RECONNECTING = "RECONNECTING"
    STALE = "STALE"            # Connected, but no updates received within stale threshold (>10s)

@dataclass(slots=True)
class ProviderTelemetry:
    provider_id: str
    state: ConnectorState
    active_subscriptions: list[str]
    last_received_ts: float | None
    last_processed_ts: float | None
    feed_freshness_ms: float | None
    reconnect_count: int
    error_count: int
    dropped_count: int
    sequence_gaps_count: int
    last_sequence_num: int | None
    wire_latency_us: float | None
    data_mode: str = "LIVE"
```

### Truthful Health Rules
1. **No Phantom Health**: A connector is NEVER marked `CONNECTED` merely because the Python object was instantiated. It requires an active TCP/TLS WebSocket handshake.
2. **Freshness Monitoring**: If `feed_freshness_ms` exceeds 10,000 ms on an active socket, the connector state transitions immediately to `STALE`.
3. **Degraded State**: If a sequence gap or parse error occurs, the connector transitions to `DEGRADED`, triggering watchdog traffic diversion.
4. **Data Mode Distinction**: Every event carries an unforgeable `data_mode` (`LIVE`, `REPLAY`, `SIMULATION`). Live feeds never emit synthetic data.

---

## 3. Backpressure & Bounded Ingress

When downstream consumers lag behind bursty market bursts (e.g. 100,000 msgs/sec during volatility events):
1. **Bounded Ring Queue**: `WebSocketFeedManager` enforces `max_queue_size` (default: 10,000 items).
2. **Deterministic Eviction**: Under queue saturation, the oldest unread frame is evicted to maintain sub-millisecond recency.
3. **Drop Tombstone Creation**: For every dropped frame, an explicit `TOMBSTONE_DROPPED` event is recorded in memory and persistent audit tables, recording `drop_count` and `venue`. Bad data is never silently discarded.
