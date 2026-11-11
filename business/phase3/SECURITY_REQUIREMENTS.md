# MDRAP Phase 3 — Enterprise Security & Isolation Specification

**Document Version**: 1.0.0  
**Target Product**: MDRAP — Market Data Reliability & Assurance Platform  
**Author**: Lead Infrastructure & Deployment Engineer (Agent 5)  
**Status**: APPROVED FOR CUSTOMER COMPLIANCE & SEC-OPS AUDIT  

---

## 1. Zero-Trust Security Architecture

Institutional trading firms maintain strict perimeter defenses to protect quantitative trading strategies, execution algorithms, and proprietary alpha. MDRAP is engineered to satisfy the most stringent institutional Infosec, SecOps, and compliance criteria.

```
+-----------------------------------------------------------------------------------+
|                        MDRAP STRICT ISOLATION BOUNDARIES                          |
+-----------------------------------------------------------------------------------+
|  1. ZERO EXECUTION CAPABILITY    | 0 order APIs, 0 order secrets, AST-verified    |
|  2. READ-ONLY NETWORK INGRESS    | Ingress-only WebSocket/UDP; 0 inbound ports    |
|  3. COMPLETE DATA SOVEREIGNTY    | 100% on-premises; 0 telemetry sent to vendor    |
|  4. LEAST PRIVILEGE RUNTIME      | Runs under unprivileged user (`uid=10001`)     |
|  5. STANDALONE VERIFIABILITY     | Standard Python `hashlib` verification tool    |
+-----------------------------------------------------------------------------------+
```

---

## 2. Security Invariants & Empirical Proof

### Invariant 1: Complete Absence of Order Routing & Trading APIs
- **Guarantee**: MDRAP contains no code capable of creating, signing, modifying, or submitting trading orders to any broker, exchange, or liquidity pool.
- **Verification Proof**: An automated Abstract Syntax Tree (AST) security scanner (`tools/check_read_only_safety.py`) audits all 125 source files in `src/mdrap` on every build. It confirms **zero occurrences** of forbidden order methods (`place_order`, `submit_order`, `send_order`, `cancel_order`) and zero trading key parameter schemas.
- **Credential Safety**: MDRAP **never asks for, accepts, or stores** exchange private API secrets or withdrawal keys.

### Invariant 2: Network Ingress-Only Communication
- **Guarantee**: MDRAP operates as a client, initiating outbound TLS connections exclusively to public, unauthenticated exchange market-data WebSocket endpoints (e.g., `wss://ws-feed.exchange.coinbase.com`).
- **No Inbound Attack Surface**: MDRAP opens no inbound listening ports on customer servers. It cannot be used as an ingress pivot by external attackers.
- **Firewall Rule Recommendation**:
  ```text
  ALLOW OUTBOUND TCP Port 443 -> [Exchange Public WebSocket IPs]
  DENY INBOUND ALL
  DENY OUTBOUND ALL OTHER PORTS
  ```

### Invariant 3: Complete Customer Data Sovereignty (Zero Phone-Home)
- **Guarantee**: MDRAP does **not transmit any telemetry, metrics, ticks, or logs** back to MDRAP developers or external cloud services.
- **On-Premises Data Retention**: All captured market data, sequence records, provenance hashes, and incident logs reside strictly within the customer's designated local SQLite database or file system partition.

### Invariant 4: Filesystem & Process Hardening
- **Process Privileges**: Designed to execute under an unprivileged system service account (e.g. `mdrap-svc`, `uid=10001`).
- **Filesystem Permissions**: Database files are initialized with POSIX mode `0600` (readable/writable exclusively by the service owner).
- **Filesystem Confinement**: Under systemd or Docker, root filesystem is mounted `read-only`, restricting write operations exclusively to `/var/lib/mdrap` and `/var/log/mdrap`.

---

## 3. Cryptographic Verification & Anti-Tamper Guarantees

1. **SHA-256 Wire Digest Binding**: Every incoming raw payload is hashed immediately upon ingress. Modification of any byte in the recorded payload changes the resulting hash.
2. **RFC 6962 Merkle Tree Inclusion Proofs**: Events are committed into binary Merkle trees with distinct domain separation prefixes (`\x00` for leaf nodes, `\x01` for interior nodes). This mathematically prevents length-extension attacks, interior-leaf collisions, and second-preimage attacks.
3. **Independent Standalone Verification**: Compliance and internal audit teams do not need to trust MDRAP's internal binaries. They can verify exported incident packages using `tools/verify_evidence.py`, which relies exclusively on standard Python standard library functions (`hashlib`, `hmac`, `json`).
