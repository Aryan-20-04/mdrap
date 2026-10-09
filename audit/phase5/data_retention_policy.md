# MDRAP Phase 5 — Data Retention and Archival Regulatory Policy

## 1. Statutory Context & Regulatory Mandate
MDRAP operates as pre-trade and trade-adjacent market data infrastructure. Data ingested, normalized, reconciled, and metered by the platform is subject to strict financial records retention mandates:

- **SEC Rule 17a-4**: Mandates broker-dealers preserve all communications and transaction records relating to business operations for not less than **six years**, the first two years in an easily accessible place, stored in WORM (Write Once, Read Many) format.
- **FINRA Rule 4511**: Mandates member firms make and preserve books and records for at least **six years**.
- **CFTC Rule 1.31**: Mandates records of all transactions relating to commodity interests and swaps be maintained for **five years**.
- **MiFID II / RTS 25**: Mandates clock synchronization records and high-frequency trade order book snapshots be retained for **five years**.

To establish absolute compliance headroom across all jurisdictions, MDRAP establishes a baseline **7-Year Retention Standard** for all market and compliance records.

---

## 2. Retention Schedule by Data Classification

| Data Category | Retention Period | Storage Format | Accessibility SLA | Regulatory Justification |
| :--- | :--- | :--- | :--- | :--- |
| **Raw IngestLog Events (`.seg`)**| **7 Years** | Zstandard-compressed archive | < 15 minutes | SEC 17a-4 / FINRA 4511 |
| **Canonical Market Data** | **7 Years** | Columnar Parquet / SQLite | < 15 minutes | Audit trail reconstruction |
| **Quarantine & Fault Records** | **7 Years** | SQLite / Merkle-sealed CSV | < 15 minutes | Compliance inspection |
| **Cross-Feed Reconciler Records**| **7 Years** | Immutable Decision Ledger | < 15 minutes | Best Execution defense |
| **Licensing Usage & Accounting** | **7 Years** | Cryptographic Merkle Ledger | < 24 hours | Exchange contract audit |
| **Operational & Telemetry Logs** | **90 Days** | Structured JSON (`.log`) | Real-time | SRE triage |
| **Ephemeral Diagnostic Bundles** | **30 Days** | Redacted JSON Bundles | Immediate | Post-incident analysis |

---

## 3. Legal Hold and Freezing Procedures

In the event of active regulatory inquiry, subpoena, or internal compliance investigation:
1. **Legal Hold Declaration**: Compliance Officer issues formal legal hold identifier (e.g., `HOLD-2026-FINRA-003`).
2. **Automated Purge Suspension**: Automated deletion and truncation crons targeting the affected date ranges or symbols are locked via:
   ```bash
   python cli.py compliance legal-hold --enable --case-id HOLD-2026-FINRA-003 --start 2026-09-01 --end 2026-10-31
   ```
3. **Immutability Guarantee**: Read-only flags applied to affected object storage containers (`s3:PutObjectLegalHold`).

---

## 4. Destruction and Cryptographic Erasure Protocol

Following the expiration of the mandatory 7-year retention period:
1. Compliance Officer signs off on the Certificate of Data Destruction.
2. Cryptographic keys used to encrypt the specific yearly archive container are deleted from the enterprise Key Management Service (KMS).
3. Underlying physical or cloud storage blocks are overwritten following NIST SP 800-88 Rev. 1 guidelines.
