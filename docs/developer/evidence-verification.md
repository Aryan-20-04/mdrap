# Cryptographic Evidence & Standalone Verification

MDRAP enables institutional compliance officers, risk auditors, and external regulators to independently verify market data integrity without requiring access to the MDRAP platform or proprietary libraries.

---

## 1. RFC 6962 Merkle Tree Specification

MDRAP constructs Merkle audit trees conforming to **RFC 6962 (Certificate Transparency)**:

- **Domain Separation**: Prevents second-preimage attacks by prefixing leaf nodes with `0x00` and interior parent nodes with `0x01`:
  $$\text{LeafHash}(D) = \text{SHA256}(0\text{x}00 \parallel D)$$
  $$\text{NodeHash}(L, R) = \text{SHA256}(0\text{x}01 \parallel L \parallel R)$$
- **Balanced Binary Structure**: When an odd number of nodes exists at a tree level, the final unpaired node is promoted to the next level without duplication.
- **Logarithmic Audit Proofs**: Proving that an event belongs to an audit batch of $N$ events requires an inclusion proof of length $\lceil \log_2 N \rceil$.

---

## 2. Exported Forensic Evidence Schema

When an incident occurs or an audit batch is sealed, MDRAP exports a self-contained evidence package:

```json
{
  "evidence_type": "INCIDENT_FORENSIC_RECORD",
  "incident_id": "inc-1700000001-0001",
  "event_id": "evt-cd75491fa9b66a44-7",
  "provider": "SYNTHETIC_BINANCE",
  "instrument": "BTC-USD",
  "data_mode": "SIMULATION",
  "timestamps": {
    "exchange_ts": 1700000000.123,
    "receive_ts": 1700000000.125,
    "processing_ts": 1700000000.126
  },
  "quality": {
    "status": "SUSPICIOUS",
    "reasons": ["SEQUENCE_GAP"]
  },
  "integrity": {
    "raw_id": "demo-raw-gap-1250",
    "payload_sha256": "3a5b...",
    "lineage_hash": "24005...",
    "merkle_root": "ee284...",
    "merkle_proof": [
      ["8f1a...", "R"],
      ["4b2c...", "L"]
    ]
  },
  "raw_wire": "{\"type\":\"ticker\",\"sequence\":1250,\"bid\":65201.0,\"ask\":65201.5}"
}
```

---

## 3. Standalone Verification Tool (`tools/verify_evidence.py`)

MDRAP provides a completely standalone Python script (`tools/verify_evidence.py`) that requires zero external dependencies and zero MDRAP packages. Any party with standard Python 3 can audit the evidence:

```bash
python tools/verify_evidence.py path/to/evidence_package.json
```

### Verification Checks Performed:
1. **Schema Integrity**: Validates presence and types of all mandatory fields.
2. **Payload Digest Verification**: Recomputes `SHA256(raw_wire)` and compares against `integrity.payload_sha256`.
3. **Lineage Hash Verification**: Recomputes the full multi-field lineage digest:
   $$\text{SHA256}(\text{event\_id} \parallel \text{raw\_id} \parallel \text{provider} \parallel \text{instrument} \parallel \text{mode} \parallel \text{payload\_sha256} \parallel \text{quality\_status} \parallel \text{processing\_ts})$$
4. **RFC 6962 Inclusion Proof**: Traverses the sibling hash path using domain separation (`0x01` interior node prefix) to reconstruct the root hash, verifying against `integrity.merkle_root`.

If any bit in the raw wire message, timestamps, normalizer status, or Merkle proof path was tampered with, verification will fail with an explicit diagnostic message.
