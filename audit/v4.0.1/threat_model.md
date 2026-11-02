# MDRAP v4.0.1 Threat Model & Security Posture

**Specification Version:** 4.0.1-THREAT-SPEC  
**Scope:** Resiliency against adversarial inputs, crash scenarios, and lock contention.  

---

## 1. Adversarial Ingress Threats

### 1.1 Massive Oversize Payloads (Denial of Service / Disk Fill)
- **Threat:** Malicious feed or compromised upstream gateway submits multi-megabyte payloads to exhaust disk space or memory.
- **Mitigation:**
  - `estimate_payload_size` inspects payload byte footprint at the Engine boundary before serialization.
  - Payloads exceeding `max_payload_bytes` are rejected immediately with offset `-1` and isolated in quarantine storage.
  - Zero allocation or WAL frame append occurs for oversized payloads.

### 1.2 Non-Serializable / Malformed Types
- **Threat:** Submitting arbitrary Python objects (e.g. `object()`) or invalid byte arrays to trigger runtime unhandled exceptions or poison JSON streams.
- **Mitigation:**
  - `IngestLog` utilizes `_json_default` to convert valid binary payloads to hex while strictly raising `TypeError` on arbitrary objects.
  - Batch append transactional wrapper rolls back write pointer on serialization failure without leaving orphan frames.

---

## 2. Infrastructure & Crash Faults

### 2.1 Torn Frames & Mid-Write Power Loss
- **Threat:** Process termination or power outage midway through writing a 28-byte frame header or payload.
- **Mitigation:**
  - Every frame contains a 32-bit CRC32 checksum over header fields and payload bytes.
  - Reader detects torn tails during recovery, safely truncating to the last valid frame without corrupting previously committed records.

### 2.2 Snapshot File Bit Rot & Tampering
- **Threat:** Storage medium bit rot or unauthorized file modification of `snapshot.json`.
- **Mitigation:**
  - Every snapshot contains a canonical SHA-256 digest over sorted state keys.
  - Checksum mismatch triggers candidate fallback.
  - If all candidates are corrupt and required WAL history is pruned, engine fails closed with `RecoveryError`.

### 2.3 Head-of-Line Blocking from External Database Locks
- **Threat:** External SQLite reader or backup daemon holds table locks (`SQLITE_BUSY`), blocking trading order book processing across all market feeds.
- **Mitigation:**
  - Ingestion lock is released before invoking projection delivery.
  - SQLite worker manages retries with exponential backoff and independent checkpoint tracking without stalling the main engine loop.
