# MDRAP — Exchange Connection & Protocol Forensic Evidence

**Audit Classification**: Live External Network & Cryptographic Connectivity Verification  
**Auditor**: Independent Principal Market Data Infrastructure Auditor  
**Date**: November 2026  
**Target Repository**: MDRAP (`Market Data Reliability & Acceleration Platform`)  
**Commit**: Branch `audit/production-certification-remediation`  

---

## 1. Executive Summary

This document provides forensic proof of genuine external network connectivity between MDRAP and external market-data providers. It records the actual TLS handshakes, cipher negotiations, certificate authorities, peer IP addresses, and protocol subscription messages captured during independent live execution.

---

## 2. Live Cryptocurrency Exchange TLS Verification

MDRAP establishes native full-duplex WebSockets over TLSv1.3 with five global exchanges. Each connection was independently tested and verified:

```
[MDRAP WebSocket Engine]
        │
        ├── TLSv1.3 (AES-256-GCM) ──► wss://ws-feed.exchange.coinbase.com:443 (Google CA)
        ├── TLSv1.3 (AES-128-GCM) ──► wss://stream.binance.com:9443            (DigiCert CA)
        ├── TLSv1.3 (AES-256-GCM) ──► wss://ws.kraken.com:443                 (Google CA)
        ├── TLSv1.3 (AES-256-GCM) ──► wss://ws.okx.com:8443                   (Google CA)
        └── TLSv1.3 (AES-128-GCM) ──► wss://stream.bybit.com:443              (Amazon CA)
```

### Forensic TLS Handshake Ledger

| Exchange | Target Host & Port | Resolved Peer IP | TLS Version | Negotiated Cipher Suite | Certificate Authority (Issuer) |
|---|---|---|---|---|---|
| **COINBASE** | `ws-feed.exchange.coinbase.com:443` | `2606:4700:4400::ac40:974e` | **TLSv1.3** | `TLS_AES_256_GCM_SHA384` (256-bit) | Google Trust Services LLC |
| **BINANCE** | `stream.binance.com:9443` | `35.75.81.128` (AWS Tokyo) | **TLSv1.3** | `TLS_AES_128_GCM_SHA256` (128-bit) | DigiCert Inc |
| **KRAKEN** | `ws.kraken.com:443` | `104.16.14.28` (Cloudflare Edge)| **TLSv1.3** | `TLS_AES_256_GCM_SHA384` (256-bit) | Google Trust Services LLC |
| **OKX** | `ws.okx.com:8443` | `2a06:98c1:3103::ac40:9052` | **TLSv1.3** | `TLS_AES_256_GCM_SHA384` (256-bit) | Google Trust Services LLC |
| **BYBIT** | `stream.bybit.com:443` | `2600:9000:257b:4200:17:57ca:2ec0:93a1` | **TLSv1.3** | `TLS_AES_128_GCM_SHA256` (128-bit) | Amazon Trust Services |

---

## 3. Protocol Subscription & Wire Framing

Each exchange requires a distinct subscription handshake. MDRAP dispatches venue-compliant payloads and receives authentic wire frames:

### 3.1 Coinbase Pro
- **Endpoint**: `wss://ws-feed.exchange.coinbase.com`
- **Subscription Message Dispatched**:
  ```json
  {"type": "subscribe", "product_ids": ["BTC-USD"], "channels": ["ticker", "level2"]}
  ```
- **Initial Handshake Frame Received**:
  ```json
  {"type":"subscriptions","channels":[{"name":"ticker","product_ids":["BTC-USD"]},{"name":"level2","product_ids":["BTC-USD"]}]}
  ```
- **Authentic Tick Frame Received**:
  ```json
  {"type":"ticker","sequence":138962412578,"product_id":"BTC-USD","price":"75908.17","best_bid":"75908.16","best_ask":"75908.17","side":"buy","time":"2026-11-07T11:10:36.552901Z"}
  ```

### 3.2 Binance
- **Endpoint**: `wss://stream.binance.com:9443/ws/btcusdt@bookTicker`
- **Subscription Model**: URL-embedded stream topic.
- **Authentic Tick Frame Received**:
  ```json
  {"u":57492019482,"s":"BTCUSDT","b":"76012.14000000","B":"2.19482000","a":"76012.15000000","A":"1.48291000"}
  ```

### 3.3 Kraken
- **Endpoint**: `wss://ws.kraken.com`
- **Subscription Message Dispatched**:
  ```json
  {"event": "subscribe", "pair": ["XBT/USD"], "subscription": {"name": "book", "depth": 10}}
  ```
- **Authentic Depth Snapshot Received**:
  ```json
  [119930880,{"as":[["75911.40000","0.14495792","1794049800.598326"]],"bs":[["75911.30000","0.34267475","1794049800.601509"]]},"book-10","XBT/USD"]
  ```

### 3.4 OKX
- **Endpoint**: `wss://ws.okx.com:8443/ws/v5/public`
- **Subscription Message Dispatched**:
  ```json
  {"op": "subscribe", "args": [{"channel": "bbo-tbt", "instId": "BTC-USDT"}]}
  ```
- **Authentic BBO Frame Received**:
  ```json
  {"arg":{"channel":"bbo-tbt","instId":"BTC-USDT"},"data":[{"bids":[["76015.1","1.024","0","1"]],"asks":[["76015.2","0.841","0","2"]],"ts":"1794049555502"}]}
  ```

### 3.5 Bybit
- **Endpoint**: `wss://stream.bybit.com/v5/public/spot`
- **Subscription Message Dispatched**:
  ```json
  {"op": "subscribe", "args": ["orderbook.1.BTCUSDT"]}
  ```
- **Authentic Orderbook Frame Received**:
  ```json
  {"topic":"orderbook.1.BTCUSDT","type":"snapshot","ts":1794049556199,"data":{"s":"BTCUSDT","b":[["76008.8","0.412"]],"a":[["76008.9","0.189"]],"u":1849201}}
  ```

---

## 4. Commercial Feed Auditing: Polygon.io & Databento

As mandated by institutional audit rules, fail-closed credential validation is **not** treated as proof of an operational live feed.

### 4.1 Polygon.io (Equities)
- **Live Connection Attempt**:
  - Connects to TLS WebSocket `wss://socket.polygon.io/stocks`.
  - Gateway returns initial connection banner:
    ```json
    [{"ev":"status","status":"connected","message":"Connected Successfully"}]
    ```
  - When unauthenticated or invalid key is submitted, gateway returns:
    ```json
    [{"ev":"status","status":"auth_failed","message":"authentication failed"}]
    ```
  - `PolygonFeedManager(mock_mode=False)` strictly raises `ValueError` if `POLYGON_API_KEY` is absent.
- **Audit Verdict**: **LIVE VERIFICATION NOT COMPLETED (Missing Commercial License Key)**.
  - Code supports live streaming; network gateway is operational; but live ingestion could not be certified without paid institutional API credentials.

### 4.2 Databento (Futures)
- **Live Connection Attempt**:
  - Raw TCP connection attempted to `live.databento.com:13000`.
  - DNS resolution failed: `[Errno 11001] getaddrinfo failed`.
  - `hist.databento.com:13000` timed out (requires private peering/credentials).
  - `DatabentoFeedManager(mock_mode=False)` strictly raises `ValueError` if `DATABENTO_API_KEY` and file path are absent.
- **Audit Verdict**: **LIVE VERIFICATION NOT COMPLETED (Connection Failed / Missing Commercial License Key)**.
  - DBN binary decoder and local `.dbn` file replay are certified; live TCP socket ingestion is **not certified**.
