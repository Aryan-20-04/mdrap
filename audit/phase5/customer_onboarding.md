# MDRAP Phase 5 — Customer and Desk Consumer Onboarding Guide

## 1. Executive Summary & Onboarding Lifecycle
This guide provides algorithmic trading desks, risk analytics groups, and quantitative researchers with the step-by-step procedure for integrating with MDRAP market data streams. The onboarding process enforces a 5-stage lifecycle ensuring technical conformance and zero operational risk prior to live production subscription.

```
[ Stage 1: Registration ] ──> [ Stage 2: Transport Selection ] ──> [ Stage 3: SDK Integration ]
                                                                             │
[ Stage 5: Production Go-Live ] <── [ Stage 4: Sandbox Conformance ] <───────┘
```

---

## 2. Stage-by-Stage Onboarding Process

### Stage 1: Tenant Registration and Entitlement Issuance
1. Desk lead files an onboarding ticket specifying:
   - Target trading strategies / algorithms.
   - Required market venues (e.g., `NASDAQ`, `BATS`, `CME`).
   - Declared usage category: `NON_DISPLAY_TRADING` or `NON_DISPLAY_RISK`.
2. SRE provisions a cryptographically hashed API token via the CLI:
   ```bash
   python cli.py security register --client-id DESK_ALPHA --role consumer --venues NASDAQ,BATS
   ```
3. Desk receives:
   - Unique `client_id` (e.g., `DESK_ALPHA`).
   - Secure API token (`mdrap_live_...`).
   - Target IP address and port (`10.200.4.10:9002` for SBE, `8000` for REST).

### Stage 2: Transport Selection Matrix
Depending on latency sensitivity and physical placement, consumers select their transport layer:

| Transport Mode | Interface | Typical Latency | Best Suited For |
| :--- | :--- | :--- | :--- |
| **Shared Memory (SHM)** | POSIX / Win32 SHM Ring Buffer | **< 1.0 µs** | Ultra-low latency co-located strategies on same physical host. |
| **Simple Binary (SBE)** | Direct TCP Streaming Socket | **< 20.0 µs** | Microsecond algorithmic execution over internal 10GbE LAN. |
| **WebSocket / JSON** | WSS Stream | **< 1.5 ms** | Real-time monitoring UIs, analytical dashboards, Python notebooks. |
| **REST / HTTP** | TLS Snapshot Endpoints | **< 5.0 ms** | Ad-hoc book queries, historical bar analytics, TCA audits. |

### Stage 3: Consumer SDK Quickstart (Python & C++ Examples)

#### Python SBE Consumer Example:
```python
import socket
import struct

# 1. Connect to SBE distribution socket
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.connect(("10.200.4.10", 9002))

# 2. Perform authentication handshake
token = "mdrap_live_abcdef1234567890abcdef1234567890"
handshake = struct.pack(">H", len(token)) + token.encode("utf-8")
sock.sendall(handshake)

# 3. Read streaming frames
while True:
    header = sock.recv(16)
    if not header:
        break
    msg_type, seq_num, ts, payload_len = struct.unpack(">HQIH", header)
    payload = sock.recv(payload_len)
    # Process market event...
```

### Stage 4: Sandbox Conformance Testing
Prior to production access, the consumer must execute against the MDRAP Conformance Simulator and demonstrate:
- Correct sequence gap detection and reporting.
- Graceful reconnection when the server terminates the socket.
- Proper handling of `SUSPICIOUS` market events without crashing.

### Stage 5: Production Go-Live
Upon successful sandbox sign-off, consumer IP is whitelisted on production firewalls, and live streaming begins.
