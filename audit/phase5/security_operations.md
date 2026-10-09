# MDRAP Phase 5 — Security Operations, Hardening, and Cryptographic Controls

## 1. Executive Summary & Security Model
As a critical pre-trade institutional infrastructure component, the Market Data Reliability & Acceleration Platform (MDRAP) must defend against data tampering, unauthorized feed interception, denial of service, and credential compromise. This document defines the operational security posture, network isolation architecture, host-level filesystem protections, cryptographic key management, and continuous security auditing practices enforced in production.

---

## 2. Network Boundary Architecture & Segmentation

MDRAP deploys across strictly segregated network zones to prevent unauthorized lateral movement:

```
┌─────────────────────────────────┐      ┌─────────────────────────────────┐
│     EXTERNAL EXCHANGE DMZ       │      │       INTERNAL TRADING LAN      │
│  - Dedicated Cross-Connects     │      │  - Low-Latency Consumer LAN     │
│  - Multicast / TCP Feed Ports   │      │  - SBE Binary Stream (Port 9002)│
└────────────────┬────────────────┘      └────────────────┬────────────────┘
                 │                                        │
                 ▼                                        ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                   MDRAP HOST INSTANCE (PROFILE A)                        │
│                                                                          │
│  - Feed Ingress Sockets: [Port 5001-5005] (Restricted to Feed IPs)      │
│  - SBE Consumer Socket:  [Port 9002] (Authenticated SBE clients)         │
│  - Local SHM Buffer:     [/dev/shm/mdrap_*] (File perms 0600)            │
│  - Management API:       [Port 8000] (Restricted to Management Subnet)   │
│  - Prometheus Metrics:   [Port 9100] (Restricted to Scraping Scanners)   │
└────────────────────────────────┬─────────────────────────────────────────┘
                                 │
                                 ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                          OPERATIONS & MGMT SUBNET                        │
│  - Bastion Host / SSH (mTLS / Ed25519)                                   │
│  - Prometheus / Grafana Servers                                          │
│  - Vault / HSM Credential Provider                                       │
└──────────────────────────────────────────────────────────────────────────┘
```

### Network Firewall Rules
- **Feed Ingress (Ports 5001–5005)**: Ingress allowed ONLY from verified exchange IP ranges / BGP peer addresses.
- **SBE Consumer Socket (Port 9002)**: Ingress allowed ONLY from designated trading desk IP subnets (`10.200.0.0/16`).
- **REST & Metrics (Ports 8000 & 9100)**: Ingress blocked from external networks; allowed only from internal monitoring subnet (`10.100.5.0/24`).

---

## 3. Host-Level Hardening and File System Security

1. **Non-Root Execution**: The MDRAP daemon runs strictly as a dedicated unprivileged system user (`mdrap:mdrap`, UID/GID `1001:1001`). No superuser privileges are granted or required.
2. **Filesystem DAC Permissions**:
   - Application binary & virtualenv: `/opt/mdrap` (Owner: `root`, Perms: `0755`, Read-only to runtime).
   - Database & WAL directory: `/var/data/mdrap/` (Owner: `mdrap`, Perms: `0700`).
   - SQLite DB files (`canonical.db`, `quarantine.db`): Owner `mdrap`, Perms `0600` (read/write only by owner).
   - Log directory: `/var/log/mdrap/` (Owner: `mdrap`, Perms: `0750`).
3. **Shared Memory Permissions**:
   - POSIX shared memory files (`/dev/shm/mdrap_*`) or Windows named file mappings created strictly with mode `0600`.
   - Consumer processes access SHM via Unix domain credentials or group membership (`mdrap_readers`).
4. **Diagnostic Bundle Sanitization**:
   - `scripts/diagnostic_bundle.py` enforces regex scrubbing of keys containing `token`, `secret`, `key`, `password`, or `salt`, preventing secret leaks in troubleshooting dumps.

---

## 4. Cryptographic Controls & Key Management

### 4.1 Token Hashing and Storage
- **Algorithm**: PBKDF2 with SHA-256 (100,000 iterations) or bcrypt.
- **Salts**: 32 bytes of cryptographically secure random bytes generated via `secrets.token_bytes(32)`.
- **Lookup Key**: A 64-bit hex prefix (`key_id = token_hash[:16]`) is used for unambiguous indexing, eliminating collision hazards and ensuring O(1) revocation without leaking token entropy.

### 4.2 Transport Layer Security (TLS)
- All HTTP REST endpoints, WebSocket connections, and Prometheus metric exporters utilize **TLS 1.3** with modern cipher suites (`TLS_AES_256_GCM_SHA384`, `TLS_CHACHA20_POLY1305_SHA256`).
- Legacy SSLv3, TLS 1.0, and TLS 1.1 are explicitly disabled at the socket layer.

### 4.3 Data-at-Rest Protection
- Database volumes on production hosts utilize hardware-accelerated NVMe self-encrypting drives (SED) or OS-level encryption (LUKS on Linux / BitLocker on Windows Server) with keys stored in the enterprise HSM.

---

## 5. Security Incident Logging & Real-Time Detection

The following security events trigger immediate alerts in the SIEM (Splunk / Elastic):
- **Authentication Failure Spike**: More than 5 failed API key attempts within 60 seconds from a single IP.
- **Unauthorized Venue Access**: Consumer attempts to subscribe to an unentitled feed venue.
- **Checksum / CRC32 Ingestion Mismatch**: Corrupted frame received on socket (potential payload injection or network glitch).
- **File Integrity Modification**: Tripwire / OSSEC alert on changes to `/opt/mdrap/` executable directory.
