# MDRAP Phase 10 — Security and Environment Preflight Audit

## 1. Security Scope & Threat Model
This audit establishes the **security, secrets isolation, and network exposure baseline** for MDRAP Phase 10 before initiating multi-node networked testing.

### Threat Model Boundaries
1. **Network Ingress Protection**: MDRAP ingest and fan-out interfaces process untrusted client streams. Ingress must enforce length framing, CRC32 checks, and schema validation.
2. **Denial of Service (DoS) Defense**: Malformed or slow consumers must not consume unbounded heap or block active publisher loops.
3. **Cluster Control-Plane Authentication**: Cluster nodes must authenticate heartbeats and verify monotonic epoch tokens to prevent unauthorized leader claims.
4. **Zero Production Secret Leakage**: Staging environments must operate with strictly isolated synthetic credentials; zero production API tokens may be stored or logged.

---

## 2. Secrets & Salt Enforcement Verification
- **API Key Salt Isolation**:
  The `SecurityManager` enforces mandatory `MDRAP_API_KEY_SALT` outside development mode. In Phase 10, a dedicated staging salt (`staging_cluster_salt_phase10_secret`) is injected via environment variables.
- **Daemon Authentication Token**:
  Control-plane administrative commands require the `MDRAP_DAEMON_TOKEN` bearer secret. Unauthenticated requests are rejected with `401 Unauthorized`.
- **Repository Credential Audit**:
  Full source tree scan confirms:
  - Zero hardcoded production exchange API keys (CME, Nasdaq, Binance, Kraken).
  - Zero private cryptographic keys in source files or configuration files.
  - All unit and integration test fixtures use ephemeral, random tokens generated via `secrets.token_hex()`.

---

## 3. Network Interface Binding & Port Security
In accordance with institutional staging requirements:
- **Default Bind Address**: All cluster daemons bind strictly to `127.0.0.1` or explicitly assigned LAN interface addresses (`10.21.12.27`).
- **Wildcard Bind Prohibition**: Binding to `0.0.0.0` is strictly prohibited to prevent accidental public WAN exposure on multihomed servers.
- **Port Allocation**:
  - Node 1: HTTP `8101`, TCP `9101`
  - Node 2: HTTP `8102`, TCP `9102`
  - Node 3: HTTP `8103`, TCP `9103`

---

## 4. Resource Bounds & Safety Limits
- **Max Quarantine Payload**: Clamped to 64 KiB (`MAX_QUARANTINE_PAYLOAD_BYTES = 65536`) to prevent memory exhaustion from oversized poisoned payloads.
- **Per-Client Queue Limits**: Consumer queues in `AsyncFanoutManager` are strictly bounded (default `maxlen=1000` to `5000`).
- **Automatic Stalled Client Eviction**: Unresponsive consumers are disconnected after exceeding `eviction_drop_threshold` (default 50 frames).

---

## 5. Security Preflight Verdict
The staging environment satisfies all institutional security preflight gates. External feed connections remain fail-closed and isolated.
