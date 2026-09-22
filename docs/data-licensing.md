# MDRAP Market Data Licensing & Vendor Compliance

This document clarifies the regulatory, legal, and operational responsibilities concerning market data feeds when deploying MDRAP.

---

## 1. Important Commercial Disclaimer

> [!IMPORTANT]
> **MDRAP is purely a software infrastructure platform.**
> **MDRAP DOES NOT PROVIDE, BROKER, RESELL, RE-DISTRIBUTE, OR SUBLICENSE MARKET DATA.**

Deploying organizations and licensees are solely and strictly responsible for:
1. Negotiating, executing, and maintaining direct commercial agreements and data licenses with all third-party market data providers and exchanges.
2. Complying with all exchange redistribution policies, display and non-display usage rules, and unit-of-count reporting obligations.
3. Managing access credentials, API keys, and private network lines directly with their vendors.

---

## 2. Customer-Managed Feed Architecture

MDRAP operates entirely on the customer's own infrastructure. All inbound market data streams connect directly from the vendor's gateway to the customer's self-hosted MDRAP container:

```
[ Market Data Vendor ]  ====== Direct Customer Connection =====>  [ Customer MDRAP Node ]
(Polygon, Databento,                                              (Runs on Customer Server)
 CME Group, Nasdaq)                                                         |
                                                                            v
                                                                 [ Validated Internal Stream ]
```

At no point does customer market data transit external third-party cloud servers or MDRAP maintainer infrastructure.

---

## 3. Supported Feed Providers & Direct Credential Configuration

Customers populate their direct vendor credentials in `.env` or system environment variables:

| Vendor / Venue | Coverage | Supported Interface | Configuration Variable |
|---|---|---|---|
| **Polygon.io** | US Equities, Options, FX, Crypto | WebSocket + REST | `POLYGON_API_KEY` |
| **Databento** | CME, Nasdaq, Cboe, OPRA Futures & Equities | Native DBN Wire / Binary | `DATABENTO_API_KEY` |
| **Coinbase Pro** | Spot Digital Assets | Secure WebSocket Feed | `COINBASE_API_KEY`, `COINBASE_API_SECRET` |
| **Binance** | Spot & Perps Digital Assets | Direct WebSocket Stream | `BINANCE_API_KEY`, `BINANCE_API_SECRET` |
| **Custom / FIX** | Direct Exchange Lines & ITCH / OUCH / FIX | Direct TCP / UDP Multicast | Pre-shared HMAC Secret |

---

## 4. Vendor Audit Readiness

MDRAP includes built-in compliance telemetry to assist customers with exchange and vendor audits:
- **Unit of Count Tracking**: All active internal consumers and API keys are indexed with timestamped access records.
- **Lineage Verification**: Every canonical market event contains immutable provenance linking back to the originating raw vendor sequence and timestamp.
- **Exportable Proofs**: Cryptographically signed audit logs (`GET /v1/audit/export`) provide proof of data handling, latency, and reconciliation without disclosing underlying trading algorithms.
