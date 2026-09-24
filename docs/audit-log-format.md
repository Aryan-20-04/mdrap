# MDRAP Cryptographic Audit Trail Specification

**Version:** 1.0.0  
**Specification Reference:** MDRAP Platform Spec §19.3 (Cryptographic Integrity & Audit Logging)  
**Standard:** Merkle-Chained SHA-256 Tamper-Evident Ledger  

---

## 1. Purpose

The MDRAP Audit Trail provides an immutable, cryptographically verifiable record of all administrative, operational, and security events across the market data infrastructure lifecycle. 

It satisfies regulatory audit standards (MiFID II RTS 25, SEC Rule 613 / Consolidated Audit Trail) by guaranteeing:
1. **Append-Only Immutability**: No historical entry can be modified, re-ordered, or deleted without invalidating all subsequent cryptographic links.
2. **Deterministic Independent Verification**: Any external auditor, counterparty, or regulator can verify the mathematical integrity of the entire ledger without requiring access to the MDRAP runtime or database engine.

---

## 2. Cryptographic Construction

The ledger is structured as a sequential cryptographic hash chain (linear Merkle chain).

### 2.1. Genesis Hash
The chain originates from a fixed 64-character genesis anchor:
```text
GENESIS_0000000000000000000000000000000000000000000000000000000000000000
```

### 2.2. Entry Serialization
For each entry $n \ge 1$, the preimage payload is a UTF-8 string formatted with strict pipe (`|`) delimiters:
$$\text{Preimage}_n = \text{prev\_hash}_n \parallel \text{"|"} \parallel \text{timestamp}_n \parallel \text{"|"} \parallel \text{actor}_n \parallel \text{"|"} \parallel \text{role}_n \parallel \text{"|"} \parallel \text{action}_n \parallel \text{"|"} \parallel \text{details}_n$$

Where:
- `prev_hash`: The 64-character lowercase hex SHA-256 hash of entry $n-1$ (or the Genesis hash for entry 1).
- `timestamp`: Monotonic Unix timestamp formatted to exactly 6 decimal places (e.g. `1788446000.123456`).
- `actor`: String identifier of the actor or process initiating the event (e.g. `system`, `operator_1`, `watchdog`).
- `role`: Role-Based Access Control tier (`VIEWER`, `OPERATOR`, `ADMIN`).
- `action`: Specific operational verb (e.g. `FEED_START`, `SOURCE_FAILOVER`, `ANOMALY_QUARANTINE`, `HMAC_ROTATION`).
- `details`: Canonical string containing event metadata and parameters.

### 2.3. Hash Derivation
The cryptographic hash for entry $n$ is computed via standard SHA-256:
$$\text{entry\_hash}_n = \text{SHA-256}(\text{Preimage}_n)$$

---

## 3. Independent Verification Algorithm

An external auditor can verify a proof using this standalone, zero-dependency Python script:

```python
import hashlib, json, sys

def verify_mdrap_proof(proof_json_path: str) -> bool:
    with open(proof_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    expected_prev = "GENESIS_0000000000000000000000000000000000000000000000000000000000000000"
    for item in data.get("entries", []):
        if item["prev_hash"] != expected_prev:
            print(f"FAILED: Link broken at entry {item['entry_id']}")
            return False

        payload = f"{item['prev_hash']}|{item['timestamp']:.6f}|{item['actor']}|{item['role']}|{item['action']}|{item['details']}"
        calculated = hashlib.sha256(payload.encode("utf-8")).hexdigest()

        if calculated != item["entry_hash"]:
            print(f"FAILED: Hash mismatch at entry {item['entry_id']}")
            return False

        expected_prev = item["entry_hash"]

    print(f"PASSED: All {len(data.get('entries', []))} audit log entries mathematically verified.")
    return True

if __name__ == "__main__":
    verify_mdrap_proof(sys.argv[1])
```

---

## 4. CLI Export & Verification Commands

```bash
# 1. Export cryptographic audit proof from running MDRAP database:
mdrap audit --export-proof audit_proof.json

# 2. Independently verify the exported proof:
mdrap audit --verify-proof audit_proof.json
```

---

## 5. Format Version 3 — Batched Tamper-Evident Quarantine Merkle Log

In addition to linear administrative audit logs, MDRAP implements high-volume, tamper-evident batch Merkle tree hashing for quarantined market data rows (`src/storage.py`).

### 5.1 Architecture & Design Rational
Quarantine bursts during market anomalies can involve thousands of discarded or malformed ticks per second. Computing a linear hash chain on the hot ingest loop would introduce serialization bottlenecks. 

MDRAP executes Merkle tree computations on the decoupled async persistence worker off the critical path:
1. **Leaf Hashing**: For each row in a flushed quarantine batch, a leaf hash is derived via SHA-256 over its canonical serialized payload:
   $$\text{leaf}_i = \text{SHA-256}(\text{payload}_i)$$
2. **Pairwise Merkle Tree**: Leaves are paired and hashed recursively up to a single 256-bit batch Merkle root:
   $$\text{parent} = \text{SHA-256}(\text{child}_L \parallel \text{child}_R)$$
   If an odd number of nodes exists at any level, the last node is duplicated.
3. **Chained Batch Ledger (`quarantine_merkle_log`)**:
   ```sql
   CREATE TABLE quarantine_merkle_log (
       batch_id INTEGER PRIMARY KEY AUTOINCREMENT,
       prev_root TEXT NOT NULL,
       merkle_root TEXT NOT NULL,
       batch_size INTEGER NOT NULL,
       created_at REAL NOT NULL
   );
   ```
4. **Audit Integration**: Every batch links into the root `audit_log` with `action="QUARANTINE_MERKLE_BATCH"`, `format_version=3`, and `details="n=<batch_size>,root=<merkle_root>"`.

### 5.2 Verification
Integrity is verified mathematically via `store.verify_quarantine_merkle_integrity()`:
```python
is_valid, count, errors = store.verify_quarantine_merkle_integrity()
assert is_valid, f"Quarantine Merkle log integrity broken: {errors}"
```
This guarantees that no historical quarantine records can be modified, scrubbed, or fabricated without breaking the cryptographic root chain.

