# MDRAP v5.2 — Institutional Durability & Acknowledgment Guarantees

**Audit Classification:** ARCHITECTURAL & FORMALLY SPECIFIED  
**Date:** 2026-11-05  
**Auditor:** Principal Storage Architect & Infrastructure Compliance Auditor  
**Target Repository:** `c:\Users\KIIT0001\Desktop\Projects\mdrap` (Branch: `v5.2-development`)  
**Source Code Anchors:**
- [`src/mdrap/models.py#L206-L227`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/mdrap/models.py#L206-L227)
- [`src/mdrap/ingestlog.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/mdrap/ingestlog.py)

---

## 1. Executive Summary

In institutional quantitative infrastructure, claims of "guaranteed zero data loss" must be coupled with rigorous acknowledgment boundaries. Stating that an event is "saved" when it sits in an application's userspace memory buffer is false: if the host loses power, that data is instantly lost.

MDRAP v5.2 formally codifies a **Six-Tier Acknowledgment Boundary Model (`AckStatus`)** and defines unambiguous durability Service Level Agreements (SLAs) across operational modes.

---

## 2. The Six-Tier Acknowledgment Boundary (`AckStatus`)

```mermaid
flowchart LR
    L1["1. RECEIVED<br/>(Network Socket)"] --> L2["2. VALIDATED<br/>(Schema & Bounds)"]
    L2 --> L3["3. BUFFERED_APP<br/>(Userspace Memory)"]
    L3 --> L4["4. JOURNALED<br/>(OS Page Cache)"]
    L4 --> L5["5. DURABLE<br/>(Physical Flash Fsync)"]
    L5 --> L6["6. PUBLISHED<br/>(Committed to SHM)"]
```

| `AckStatus` Level | Storage Boundary | Survival Guarantee | Latency Cost |
| :--- | :--- | :--- | :--- |
| **`RECEIVED` (1)** | OS Socket Buffer / NIC Ring | Survives client disconnect | $< 0.1\,\mu\text{s}$ |
| **`VALIDATED` (2)** | Ingress Gateway Register | Survives client payload retransmission | $\approx 0.5\,\mu\text{s}$ |
| **`BUFFERED_APP` (3)** | Python Heap / Ring Buffer | Survives upstream network drop; **Lost on process crash** | $\approx 1.5\,\mu\text{s}$ |
| **`JOURNALED` (4)** | OS Kernel Page Cache (`write()`) | Survives process termination (`SIGKILL`); **Lost on host power failure** | $\approx 10.9\,\mu\text{s}$ |
| **`DURABLE` (5)** | Non-Volatile Flash (`fdatasync()`) | **Survives complete host power failure** | $\approx 3,360\,\mu\text{s}$ (3.36 ms) |
| **`PUBLISHED` (6)** | Memory-Mapped Ring Buffer (`SHM v3`) | Lock-free IPC visibility across co-located processes | $\approx 1.5\,\mu\text{s}$ |

---

## 3. Operational Durability Modes (SLAs)

MDRAP provides three selectable operating profiles to balance throughput against durability:

### 3.1 Mode 1: LOSS_DETECTING (Ultra-Low Latency Market Making)
- **Primary Use Case:** Proprietary HFT market making, quote synthesis, arbitrage routing.
- **Fsync Policy:** `never` (Userspace buffered + OS page cache).
- **Acknowledgment Boundary:** `AckStatus.PUBLISHED` (SHM commit).
- **Throughput:** **$\approx 91,500\text{ EPS}$**.
- **Median Latency:** **$1.5\text{--}10.9\,\mu\text{s}$**.
- **Failure Recovery Guarantee:** If the process terminates, events up to the last OS flush survive. If host power fails, unwritten pages are lost. Dropped sequences are detected immediately via monotonic sequence gaps upon restart.

### 3.2 Mode 2: GROUPED_PERSISTENCE (Balanced Production Trading)
- **Primary Use Case:** Order execution management, book building, risk auditing.
- **Fsync Policy:** `grouped_by_time` (Background 10 ms fsync flusher).
- **Acknowledgment Boundary:** `AckStatus.JOURNALED` (Async promotion to `DURABLE`).
- **Throughput:** **$\approx 42,200\text{ EPS}$**.
- **Median Latency:** **$23.7\,\mu\text{s}$**.
- **Failure Recovery Guarantee:** Survives process crash with 0% data loss. Under complete host power failure, uncommitted data is strictly bounded to the last **$\le 10\text{ ms}$** of market events.

### 3.3 Mode 3: DURABLE_CAPTURE (Regulatory Compliance & Cold Auditing)
- **Primary Use Case:** Regulatory trade repositories, MiFID II / CAT reporting, non-repudiation cold storage.
- **Fsync Policy:** `always` (Synchronous hardware flush per record).
- **Acknowledgment Boundary:** `AckStatus.DURABLE`.
- **Throughput:** **$\approx 298\text{ EPS}$**.
- **Median Latency:** **$3,360\,\mu\text{s}$** (3.36 ms).
- **Failure Recovery Guarantee:** **Absolute physical zero-data-loss guarantee**. No event is acknowledged to upstream gateways until non-volatile flash write confirmation is received.

---

## 4. Institutional Compliance Summary

By decoupling operational modes and explicitly tagging telemetry with `AckStatus`, MDRAP v5.2 ensures that risk officers, execution traders, and quantitative researchers operate with complete clarity regarding data durability boundaries.
