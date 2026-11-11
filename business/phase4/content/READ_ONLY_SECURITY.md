# Read-Only Security Architecture: Zero Trading Keys, Zero Execution Risk

**Audience**: Chief Information Security Officers (CISOs), Risk Committees, Infrastructure Security Leads  
**Topic**: Defensive Boundary Architecture of the MDRAP Market-Data Sidecar  

---

## 1. Executive Summary

Financial infrastructure tools often request broad operational permissions: API keys with trading privileges, database write credentials, and outbound internet access. When third-party monitoring platforms are compromised, these credentials present a direct, catastrophic vulnerability: unauthorized order execution, balance drain, or flash crashes.

MDRAP was architected under a **Zero-Trust Read-Only Security Model**:
- It requires **zero private trading credentials**.
- It possesses **zero order routing, execution, or modification code**.
- It connects strictly to **public market-data endpoints**.
- It operates with a **hardened container profile** dropping all Linux capabilities.

---

## 2. The Four Pillars of MDRAP Security

### Pillar 1: Total Absence of Order Routing Logic
MDRAP is not an Order Management System (OMS) with execution features disabled; it contains zero execution code in its source repository. There are no FIX trading handlers, no order submission data models, and no exchange order placement endpoints. Even if an attacker obtained full control of the MDRAP process, they could not place an order through it.

### Pillar 2: Read-Only Public Exchange Connectors
Supported exchange connectors (Coinbase, Kraken, Binance, Databento) bind strictly to unauthenticated public WebSocket ticker, trade, and depth streams. These endpoints never require API keys, passphrases, or HMAC signing secrets.

### Pillar 3: Cryptographic Salt & Secret Enforcement
When local REST or WebSocket APIs are enabled for sidecar queries, MDRAP requires an explicit, cryptographically random 32-byte salt (`MDRAP_API_KEY_SALT`). The daemon detects and rejects default, empty, or low-entropy salts at startup.

### Pillar 4: Hardened Container & Process Isolation
The official production container configuration enforces least-privilege standards:
- Runs as an unprivileged user (`mdrap`, UID 10001).
- All Linux kernel capabilities are dropped (`cap_drop: [ALL]`).
- The root filesystem is mounted strictly read-only (`read_only: true`).
- Writes are restricted to a local `/data` directory for SQLite WAL storage.
- APIs default strictly to the loopback interface (`127.0.0.1:8000`).

---

## 3. Auditing and Verifiability

Because MDRAP produces **RFC 6962 Merkle inclusion proofs**, compliance and security teams do not need to trust MDRAP's internal binaries. Any exported evidence package can be verified independently on an air-gapped machine using standard Python:

```bash
python tools/verify_evidence.py evidence_inc-*.json
```

This ensures mathematical finality without vendor lock-in or proprietary verification daemons.
