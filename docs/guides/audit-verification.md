# Audit Verification Guide

## Goal
Verify the cryptographic SHA-256 Merkle chain integrity of persisted market data and administrative actions, inspect quarantined data lineage, and export tamper-evident verification packages for internal risk control or regulatory inquiries.

## Prerequisites
- Python 3.10+
- `mdrap-core` installed
- Access to an MDRAP SQLite database file (`mdrap.db`) or running API service

## Steps

### Step 1: Programmatic Audit Verification
Verify the rolling SHA-256 Merkle chain directly using the storage engine:

<!-- doc-test: python -->
```python
# doc-test
import tempfile
from mdrap.storage import Store

# Initialize test database with audit records
db_path = tempfile.mktemp(suffix=".db")
store = Store(db_path)

# Record an operational action
entry_hash = store.append_audit(
    actor="gateway_service",
    role="OPERATOR",
    action="INGEST_COMMITTED",
    details="symbol=AAPL,price=150.25,seq=101",
)

# Cryptographically verify the complete audit hash chain
valid, message, count = store.verify_audit_integrity()

print(f"Chain Valid: {valid}")
print(f"Verified Entries: {count}")
print(f"Latest Hash: {entry_hash[:16]}...")

store.close()
```

### Step 2: CLI Audit Verification & Export
You can also verify integrity and export audit bundles using the `mdrap audit` CLI command:

```bash
# Verify SQLite audit log integrity
mdrap audit verify --db ./data/mdrap.db

# Export signed audit package
mdrap audit export --db ./data/mdrap.db --out ./audit_package.zip
```

### Step 3: Inspect Quarantined Events
When an event violates market quality rules, it is quarantined with full lineage to ensure no silent data loss:

```python
from mdrap.storage import Store

store = Store("./data/mdrap.db")
quarantined = store.query_quarantine(limit=50)

for record in quarantined:
    print(f"Event ID: {record['event_id']}")
    print(f"Violations: {record['reasons']}")
    print(f"Raw Wire ID: {record['raw_id']}")
```

## Expected Output
```text
Chain Valid: True
Verified Entries: 1
Latest Hash: a0e2b89c5fe5d6fd...
```

## Troubleshooting
- **Audit Verification Fails (`valid == False`)**: If an unauthorized process modifies rows in SQLite directly without updating the rolling hash chain, `verify_audit_integrity()` detects the discrepancy and points to the earliest corrupt sequence index.
- **Empty Audit Log**: If `count == 0`, no audit actions have been recorded yet. The chain is trivially valid.

## Next Steps
- Review the full hashing and Merkle tree specification in [docs/audit-log-format.md](../audit-log-format.md).
- Inspect role-based access control and token management in [Security Architecture](../security.md).
