# MDRAP Phase 5 — Licensing Operations, Usage Accounting, and Compliance Audit

## 1. Executive Summary & Regulatory Framework
Major market data exchanges (e.g., NASDAQ, NYSE, CME, ICE) enforce strict reporting and licensing obligations for redistributors and consumers. Under institutional market-data contracts, data access is categorized into **Display Use** (human traders) and **Non-Display Use** (automated trading, risk calculation, TCA, algorithmic pricing).

MDRAP operationalizes real-time, deterministic per-consumer usage accounting and compliance reporting directly within the distribution kernel. This document defines the operational procedures for daily usage aggregation, reconciliation, tamper-evident audit trail verification, and automated exchange compliance submission.

---

## 2. Usage Accounting Architecture & Data Model

Every event distributed over SBE sockets, shared memory, or REST/WebSocket endpoints updates an in-memory atomic counter structure per `(client_id, venue, symbol, usage_type)`:

```
[ Downstream Consumer ] ──(SBE Request)──> [ Entitlement Guard (PBKDF2 Token) ]
                                                            │
                                                   (Permitted: Token Valid)
                                                            ▼
                                               [ Atomic Metering Registry ]
                                                            │
                      ┌─────────────────────────────────────┴─────────────────────────────────────┐
                      ▼                                                                           ▼
           [ Daily Usage DB Table ]                                                    [ Real-Time Metrics ]
           (SQLite `usage_metering`)                                                   (`mdrap_metered_ticks_total`)
```

### Metering Schema Fields (`usage_metering`)
- `record_id`: Auto-increment integer.
- `session_date`: ISO 8601 Date (`YYYY-MM-DD`).
- `client_id`: Institutional tenant identifier (e.g., `DESK_ALPHA_QUANT`).
- `venue`: Exchange feed origin (`NASDAQ`, `NYSE`, `BATS`, `CME`).
- `symbol`: Ticker symbol or asset identifier (`AAPL`, `ESZ6`).
- `usage_type`: `NON_DISPLAY_TRADING`, `NON_DISPLAY_RISK`, `DISPLAY_TERMINAL`.
- `event_count`: Total tick events successfully dispatched.
- `byte_count`: Raw binary payload bytes transmitted.
- `metering_hash`: SHA-256 HMAC of the record tuple incorporating the secret audit key.

---

## 3. Daily End-of-Day (EOD) Reconciliation Procedure

At market close (16:30:00 EST / 21:30:00 UTC), the automated reconciliation batch job triggers:

```bash
# Execute automated daily usage accounting consolidation
python cli.py accounting reconcile --date $(date +%Y-%m-%d) --export-dir /var/data/mdrap/compliance/
```

### Detailed EOD Steps:
1. **Freeze Active Window**: Snapshot in-memory atomic counters to the local disk queue.
2. **Cross-Reconcile with IngestLog**: Verify that total metered events per venue does not exceed total raw canonical events ingested:
   $$\text{Total Distributed Ticks} \le \text{Ingested Canonical Ticks} \times N_{\text{consumers}}$$
3. **Generate Merkle Root**: Hash all EOD client records into a cryptographic Merkle tree.
4. **Export Signed Reports**: Write exchange-compliant CSV manifests to `/var/data/mdrap/compliance/reports/`.
5. **Archive & Hash Lock**: Compress daily records and store the Merkle root in the append-only `merkle_audit` table.

---

## 4. Compliance Export Formats

MDRAP automatically generates standardized reporting files for major exchanges:

### 4.1 NASDAQ Global Data Products Format (`nasdaq_report_YYYYMM.csv`)
```csv
ReporterOrg,SubscriberID,DataFeedID,CategoryCode,DeviceCount,NonDisplayCount,BillingMonth
MDRAP_INST_01,DESK_ALPHA,NQ_ITCH_L1,ND_CAT_1,0,1,2026-10
MDRAP_INST_01,DESK_BETA_TCA,NQ_ITCH_L2,ND_CAT_3,0,1,2026-10
```

### 4.2 NYSE Unit-of-Count (UOC) Format (`nyse_report_YYYYMM.csv`)
```csv
VendorAcct,ClientLegalName,FeedCode,ServiceType,UnitCount,Status
VEN-9942,Alpha Capital Strategies,NYSE_PILLAR,NON_DISPLAY,1,ACTIVE
VEN-9942,Beta Risk Execution,NYSE_PILLAR,NON_DISPLAY,1,ACTIVE
```

---

## 5. Audit Defense and Verification Protocol

During an exchange audit, MDRAP operators can prove non-repudiation and exact billing counts within seconds:

```bash
# Verify cryptographic audit trail for a specific trading session
python cli.py accounting verify-audit --date 2026-10-09 --report /var/data/mdrap/compliance/reports/daily_2026-10-09.json
```

**Verification Guarantees**:
1. **Zero Under-reporting**: The consumer dispatch kernel will fail closed (halt delivery) if the metering counter cannot be incremented.
2. **Cryptographic Sealing**: HMAC-SHA256 signatures prevent offline tampering with historical database records.
3. **Audit Readiness**: Historical records retained for 7 years per FINRA Rule 4511 and SEC Rule 17a-4.
