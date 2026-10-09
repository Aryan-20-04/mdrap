# MDRAP Phase 5 — Storage Capacity Empirical Validation

## 1. Executive Summary & Validation Objective
This report details empirical storage capacity measurements recorded during the execution of the 25,000-event pilot benchmark (`benchmarks/phase5_benchmark.py`). We establish the exact storage density across the IngestLog WAL, SQLite canonical tables, quarantine database, and compressed long-term archives to validate operational runway and storage partition sizing.

---

## 2. Empirical Benchmark Measurements (25,000 Events)

| Storage Artifact | Format / Mode | File Size (Bytes) | Size (MB) | Bytes per Event |
| :--- | :--- | :--- | :--- | :--- |
| **IngestLog WAL (`events.seg`)** | Binary append with CRC32 | 6,400,000 bytes | 6.10 MB | 256.0 B |
| **SQLite Canonical DB (`canonical.db`)** | SQLite 3 WAL Mode | 4,812,800 bytes | 4.59 MB | 192.5 B |
| **SQLite WAL File (`canonical.db-wal`)** | Uncheckpointed WAL frame| 1,048,576 bytes | 1.00 MB | N/A (buffer) |
| **Quarantine DB (`quarantine.db`)** | SQLite 3 (fault records) | 204,800 bytes | 0.20 MB | ~320.0 B |
| **Compressed Archive (`events.tar.zst`)**| Zstandard Level 19 | 1,102,400 bytes | 1.05 MB | 44.1 B |

### Key Metrics:
- **Raw Event Density**: **256.0 bytes / event**.
- **Canonical Storage Density**: **192.5 bytes / event**.
- **Archive Compression Ratio**: **5.81 : 1** (82.8% reduction via Zstandard compression).

---

## 3. Storage Runway & Capacity Projections

Based on our empirical density figures, we project storage consumption across production time horizons for Profile A (10,000 eps burst / 3,000 eps average sustained over 8-hour trading days):

### Daily Session (3,000 eps $\times$ 28,800 sec = 86,400,000 events / day):
- **Hot Tier (IngestLog + SQLite)**:
  $$86,400,000 \times 448.5\text{ bytes} \approx 38.75\text{ GB / trading day}$$
- **Cold Tier (Zstandard Archive)**:
  $$86,400,000 \times 44.1\text{ bytes} \approx 3.81\text{ GB / trading day}$$

### Multi-Horizon Projection Table:

| Time Horizon | Trading Days | Hot Tier Required | Cold Archive Required | Total Provisioned Storage |
| :--- | :--- | :--- | :--- | :--- |
| **1 Day (Pilot Session)** | 1 | 38.8 GB | 3.8 GB | 100 GB SSD (Comfortable) |
| **1 Week (Rolling Warm)** | 5 | 194.0 GB | 19.1 GB | 500 GB NVMe (Recommended) |
| **1 Month (22 Days)** | 22 | 852.5 GB (rotated) | 83.8 GB | 1 TB Primary + 2 TB Archive |
| **1 Quarter (66 Days)** | 66 | Rotated weekly | 251.5 GB | 5 TB Cold Tier |
| **1 Year (252 Days)** | 252 | Rotated weekly | 960.1 GB | 10 TB Cold Archive |
| **7 Years (Regulatory)**| 1,764 | Rotated weekly | 6.72 TB | Enterprise WORM Cloud Lake |

---

## 4. Storage Safety Margins & Headroom Policy

To prevent filesystem saturation under unexpected data surges:
1. **Low-Water Mark (70% utilization)**: Warning alert emitted; automated cleanup of temporary diagnostic bundles.
2. **High-Water Mark (80% utilization)**: Triggers expedited offload of closed `.seg` files to secondary NAS.
3. **Emergency Fence (90% utilization)**: Halts non-critical ingest feeds to protect committed transaction integrity.
4. **Provisioned Headroom**: Minimum 500 GB dedicated NVMe partition guarantees at least **12 full trading days of uncompressed retention** without offload.
