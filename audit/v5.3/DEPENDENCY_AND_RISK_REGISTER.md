# MDRAP v5.3 — Dependency & Institutional Risk Register

## Overview
This document inventories external dependencies, platform security posture, runtime risks, and mitigation strategies for institutional production deployment.

---

## 1. External Dependency Audit

MDRAP adheres to a minimal-dependency architecture. The core pipeline is designed to run entirely on Python standard library and compiled C primitives.

| Component | Dependency | Version | License | Security / Vulnerability Audit | Justification |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Core Ingestion** | Python stdlib (`ctypes`, `mmap`, `socket`) | 3.13.1 | PSF | Clean | Zero external dependencies on hot path |
| **CLI Display** | `rich` | $\ge 13.0$ | MIT | Clean; no known CVEs | Terminal UI rendering & formatting |
| **Testing** | `pytest`, `pytest-cov`, `pytest-asyncio` | Latest | MIT | Dev-only | Automated test execution |
| **Native Kernel** | C99 Standard Library | MSVC 19.42 / GCC / Clang | — | Zero dynamic runtime dependencies | High-throughput SBE / SHM / Fastpath |

---

## 2. Institutional Risk Register

| Risk ID | Risk Description | Likelihood | Impact | Current Mitigation | Residual Risk |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **R-01** | **Live Exchange API Credential Leak** | LOW | CRITICAL | Real-money execution permanently disabled platform-wide. API keys hashed with salt. | Minimal |
| **R-02** | **SHM Permissions Escalation** | LOW | HIGH | SHM segments locked with strict OS permissions (`0600`). Non-owner access rejected. | Low |
| **R-03** | **Unhedged Inventory Runaway** | MEDIUM | HIGH | Pre-trade risk gates (G03, G04) enforce gross (\$500k) and net (\$250k) position caps. | Low |
| **R-04** | **Adverse Selection on Lag Spikes** | HIGH | MEDIUM | Microstructure threshold ($> 2.0\text{ ms}$) triggers automated order cancellations via `cancel_all()`. | Controlled |
| **R-05** | **Storage Volume Exhaustion (ENOSPC)** | LOW | HIGH | Rolling WAL segmentation with bounded retention and automatic compression. | Low |
