# MDRAP Phase 6 — Storage Capacity Model, IOPS Sizing, and Hardware Requirements

## 1. Executive Summary & Objective
To prevent unexpected disk saturation and guarantee deterministic I/O latency under institutional trading volumes, this document establishes the mathematical storage capacity model, IOPS provisioning requirements, and disk footprint projections for MDRAP across production profiles.

---

## 2. Event Sizing & Ingestion Mathematics

### Baseline Event Sizes
- **Raw Binary SBE Record**: 142 bytes / event
- **WAL IngestLog Record (Header + CRC32 + SBE)**: 164 bytes / event
- **SQLite Database Record (Indexed)**: 206 bytes / event
- **Compacted Cold Archive (Zstd-19)**: ~24.5 bytes / event (5.81 : 1 compression)

### Daily Trading Volume Calculations
Assuming an active trading session of **6.5 hours** ($23,400\text{ seconds}$):
$$\text{Events}_{\text{day}} = \text{Throughput (eps)} \times 23,400\text{ s}$$

| Ingest Rate (eps) | Daily Events | Raw WAL Volume / Day | SQLite Volume / Day | Compacted Cold Volume / Day |
| :--- | :--- | :--- | :--- | :--- |
| **1,000 eps** | 23.4 Million | 3.84 GB | 4.82 GB | **0.57 GB** |
| **5,000 eps** | 117.0 Million | 19.19 GB | 24.10 GB | **2.87 GB** |
| **10,000 eps** | 234.0 Million | 38.38 GB | 48.20 GB | **5.73 GB** |
| **20,000 eps (Profile A)** | 468.0 Million | **76.75 GB** | **96.41 GB** | **11.47 GB** |
| **50,000 eps (Peak)** | 1.17 Billion | 191.88 GB | 241.02 GB | **28.67 GB** |

---

## 3. Tiered Storage Footprint Projections (Profile A: 20,000 eps)

Assuming 22 trading days per month (264 trading days per year):

| Storage Window | Active Tier | Retention Policy | Projected Data Footprint | Storage Medium |
| :--- | :--- | :--- | :--- | :--- |
| **Current Day** | Hot Tier | Uncompressed WAL + SHM | **76.8 GB** | Local NVMe SSD |
| **30 Days** | Warm Tier | SQLite DBs + WAL buffer | **2.12 TB** | Local Enterprise SSD |
| **1 Year** | Cold Tier | Compacted Zstd Parquet | **3.03 TB** | S3 / GCS Standard |
| **7 Years (SEC 17a-4)** | Archive Tier | Compacted Zstd + Merkle Roots | **21.20 TB** | S3 Glacier / Coldline |

---

## 4. IOPS & Bandwidth Provisioning Requirements

### Write Throughput Requirements at 20,000 eps:
$$\text{Ingest Bandwidth} = 20,000 \times 164\text{ bytes} = 3.28\text{ MB/sec}$$
$$\text{SQLite Drainer Write Bandwidth} = 20,000 \times 206\text{ bytes} = 4.12\text{ MB/sec}$$
$$\text{Total Continuous Write Bandwidth} = \mathbf{7.40\text{ MB/sec}}$$

### IOPS Calculations:
- **Sequential IngestLog Appends**: 4 KB write buffer $\rightarrow 820\text{ IOPS}$ sequential.
- **SQLite Batched Executemany (500 events/batch)**: 40 commits/sec $\rightarrow 160\text{ IOPS}$ sequential WAL write.
- **Minimum Recommended Disk IOPS**: **3,000 IOPS** (easily satisfied by standard NVMe or AWS gp3/io2).
- **Minimum Recommended Burst IOPS**: **10,000 IOPS** (to handle market open/close volume spikes).

---

## 5. Filesystem & OS Recommendations
1. **Filesystem**: `XFS` or `ext4` with `noatime,nodiratime` mount options on Linux; `NTFS` with `DisableLastAccessUpdate=1` on Windows.
2. **I/O Scheduler**: `none` (NVMe) or `mq-deadline`.
3. **Partition Separation**: Dedicated physical NVMe drive for hot `/var/data/mdrap/wal/` distinct from OS root disk to avoid I/O starvation.
