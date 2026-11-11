# MDRAP Pilot Readiness Audit — Agent 1: Event Provenance & Durability Verification

**Agent Role:** Lead Auditor for Event Provenance, Wire Durability & Persistence  
**Target:** `src/mdrap/provenance.py`, `src/mdrap/ingestlog.py`, `src/mdrap/storage.py`, `src/mdrap/incidents.py`  
**Evaluation Standard:** Production Pilot Trust Standard & Forensic Reproducibility  
**Date:** 2026-11-11  

---

## 1. Executive Summary

Agent 1 conducted an exhaustive, code-level and executable verification of the event provenance and durability subsystem in the `product-focus/mdrap-reliability-platform` branch. 

While the in-memory data structures (`EventProvenance`, `compute_payload_hash`, `lineage_hash`) are well-designed and deterministic during an active Python process runtime, **the provenance subsystem currently fails the core durability invariant across application restarts**:
> *"A trading firm must be able to connect MDRAP to its existing feeds in read-only mode, detect a genuine data-quality problem, preserve the evidence, reconstruct the event sequence, and independently verify what happened—**even after MDRAP restarts**."*

Empirical testing proved that when MDRAP is restarted:
1. `ProvenanceTracker.get(event_id)` fails to return records not explicitly saved to the legacy lineage table.
2. Even when legacy lineage records exist, critical cryptographic fields (`payload_sha256`, `channel`, `data_mode`, `sequence_number`) are **lost** because the SQLite schema lacks a first-class `event_provenance` table.
3. Raw message wire payloads are preserved only if an explicit `RawArchive` or `IngestLog` WAL is concurrently bound; standard SQLite ingestion paths do not persist raw wire payloads for canonical events.

---

## 2. Source Code & Architectural Findings

### Finding 1.1: Volatile In-Memory Provenance Storage (`src/mdrap/provenance.py:128-142`)
- **Status:** **CONFIRMED DEFECT (HIGH SEVERITY)**
- **File / Lines:** [`src/mdrap/provenance.py:128-142`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/mdrap/provenance.py#L128-L142)
- **Code:**
  ```python
  class ProvenanceTracker:
      def __init__(self, max_records: int = 50_000, store: Any = None):
          self.max_records = max_records
          self.store = store
          self._records: dict[str, EventProvenance] = {}
          self._order: list[str] = []

      def record(self, prov: EventProvenance) -> None:
          if len(self._order) >= self.max_records:
              oldest = self._order.pop(0)
              self._records.pop(oldest, None)
          self._records[prov.event_id] = prov
          self._order.append(prov.event_id)
  ```
- **Evidence:** `ProvenanceTracker.record()` writes strictly to `self._records` (heap dictionary). It never invokes `self.store` or any persistent database write. On process termination or restart, all in-memory provenance records are permanently wiped out.

### Finding 1.2: Cryptographic Information Loss on Restart Fallback (`src/mdrap/provenance.py:147-174`)
- **Status:** **CONFIRMED DEFECT (HIGH SEVERITY)**
- **File / Lines:** [`src/mdrap/provenance.py:147-174`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/mdrap/provenance.py#L147-L174)
- **Code:**
  ```python
  if self.store and hasattr(self.store, "event_lineage"):
      raw_lin = self.store.event_lineage(event_id)
      if raw_lin:
          return EventProvenance(
              ...
              channel="UNKNOWN",
              data_mode="HISTORICAL",
              exchange_timestamp=0.0,
              sequence_number=None,
              payload_sha256="",
              ...
          )
  ```
- **Evidence:** When `get(event_id)` misses in-memory records (such as after restart), it queries `store.event_lineage()`. Because the underlying `lineage` SQLite table (`storage_schema.py:76-88`) only holds high-level reconciliation metadata, the reconstructed record sets `payload_sha256=""`, `exchange_timestamp=0.0`, `channel="UNKNOWN"`, and `sequence_number=None`. The cryptographic lineage hash is consequently corrupted or impossible to verify against raw wire data.

### Finding 1.3: Pipeline Disconnect with Provenance Creation
- **Status:** **CONFIRMED ARCHITECTURAL DEFECT (MEDIUM SEVERITY)**
- **File / Lines:** `src/mdrap/pipeline.py` (throughout)
- **Evidence:** `pipeline.py` normalizes raw frames into `CanonicalEvent` and writes batches to `store.write_canonical_batch()`, but does not invoke `create_provenance()` or record into a shared `ProvenanceTracker`. Event provenance must be instantiated and bound as a mandatory stage in the ingestion pipeline.

---

## 3. Empirical Test & Proof of Defect

A standalone verification test was executed (`audit/pilot-readiness/test_audit_provenance.py`):
```text
In-memory payload_sha256: 3ac0bfa0125118a5bdf4f77d2c6617cb2235ab6b8e7f2affd2ea1c08235df5ac
In-memory lineage_hash: 32ff7d909a6059406745d8751a949c4fee02419af65a16293399d396fbcd1c28
After restart get(evt-1): None
```
**Conclusion:** Provenance records did not survive process restart.

---

## 4. Required Remediation for Pilot Readiness

1. **Persistent Table Schema (`event_provenance`)**:
   Add an `event_provenance` SQLite table to store complete forensic records:
   - `event_id TEXT PRIMARY KEY`
   - `raw_id TEXT`
   - `provider TEXT`
   - `instrument TEXT`
   - `channel TEXT`
   - `data_mode TEXT`
   - `exchange_timestamp REAL`
   - `receive_timestamp REAL`
   - `processing_timestamp REAL`
   - `sequence_number INTEGER`
   - `parser_version TEXT`
   - `normalizer_version TEXT`
   - `quality_status TEXT`
   - `quality_reasons TEXT`
   - `payload_sha256 TEXT`
   - `processing_status TEXT`
   - `durability_status TEXT`
   - `lineage_hash TEXT`
   - `raw_wire TEXT` (optional or truncated payload for complete auditability)
2. **Durability in `ProvenanceTracker.record()`**:
   If `self.store` or `self.db_path` is configured, automatically persist provenance records to SQLite in WAL mode.
3. **Lossless Recovery in `ProvenanceTracker.get()`**:
   Query the persistent `event_provenance` table first before falling back to legacy `lineage`.
