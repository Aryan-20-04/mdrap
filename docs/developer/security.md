# Security Architecture & Assurance Model

MDRAP is designed from the ground up as a **zero-trust, read-only security boundary**. It is architected specifically to eliminate the catastrophic risks associated with storing trading execution credentials in monitoring tools.

---

## 1. Non-Negotiable Security Invariants

1. **Zero Order Execution Capability**: The codebase contains zero order placement, routing, modification, or cancellation logic. There are no trading endpoints, no FIX trading session handlers, and no private API key storage mechanisms.
2. **Read-Only Public Connectors**: Exchange connectors bind strictly to public ticker, trade, and order book channels. They do not request or support API secret passphrases.
3. **Mandatory Cryptographic Salt**: Hashing routines and API token storage require an explicit 32-byte cryptographic salt (`MDRAP_API_KEY_SALT`). The application refuses to initialize if default or empty salts are detected.
4. **Local Loopback Isolation**: By default, the REST and WebSocket APIs bind exclusively to `127.0.0.1`.
5. **Least-Privilege Execution**: The official Docker container drops all Linux capabilities (`cap_drop: [ALL]`) and executes under an unprivileged user (`mdrap`, UID 10001).

---

## 2. Threat Modeling & Attack Surface Analysis

| Threat Vector | Potential Impact | MDRAP Defensive Control |
| :--- | :--- | :--- |
| **Credential Theft** | Unauthorized trading or balance drain | **Impossible**: MDRAP never requests or stores private trading keys. |
| **Upstream Feed Poisoning** | Bad ticks tricking algorithmic strategies | **15-Rule Quality Engine**: Detects crossed spreads, sequence gaps, and price spikes, quarantining ticks before trading engines consume them. |
| **Historical Data Tampering** | Concealing trading errors or compliance breaches | **RFC 6962 Merkle Trees**: Binds every event and incident to an immutable Merkle root; tampering invalidates inclusion proofs. |
| **Denial of Service / Network Spike** | Sidecar crashing during market volatility | **Lockless Ring Buffers & SQLite WAL**: Ingestion decoupled from downstream consumers; batched WAL writes prevent disk lockups. |
| **Privilege Escalation** | Container breakout to host system | **Hardened Container & systemd Profile**: Read-only root filesystem, non-root user, `NoNewPrivileges=true`, `ProtectSystem=strict`. |

---

## 3. Auditing & Compliance Verification

Because MDRAP produces RFC 6962 Merkle proofs, compliance officers and external regulators can audit exported evidence packages without trusting internal platform binaries:

```bash
python tools/verify_evidence.py demo/output/evidence_inc-*.json
```

This verification runs in pure Python with zero dependencies, validating the mathematical authenticity of the forensic record.
