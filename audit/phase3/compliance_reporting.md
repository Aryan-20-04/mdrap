# MDRAP Phase 3 — Compliance Reporting Specification

**Document Identifier**: `MDRAP-REPORT-P3-001`  
**Date**: October 9, 2026  

---

## 1. Export Formats

MDRAP generates automated compliance audit reports in two formats:

### JSON Specification (RFC 8259):
```json
{
  "report_version": "1.0",
  "generated_at": 1728475200.0,
  "query_window": {
    "start_ts": 1728471600.0,
    "end_ts": 1728475200.0
  },
  "line_items": [
    {
      "tenant_id": "Tenant_Alpha",
      "client_id": "Client_1",
      "source": "NASDAQ",
      "symbol": "AAPL",
      "unit": "DISTRIBUTED_EVENT",
      "total_count": 105000,
      "batch_count": 210
    }
  ]
}
```

### CSV Specification (RFC 4180):
```csv
tenant_id,client_id,source,symbol,unit,total_count,batch_count
Tenant_Alpha,Client_1,NASDAQ,AAPL,DISTRIBUTED_EVENT,105000,210
```

---

## 2. Retention & Auditability

Usage records are retained indefinitely or partitioned into monthly SQLite databases. Cryptographic hashes of daily usage summaries can be written to the tamper-evident Merkle audit chain.
