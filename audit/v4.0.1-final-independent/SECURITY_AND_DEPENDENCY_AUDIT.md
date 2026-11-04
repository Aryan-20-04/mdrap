# MDRAP v4.0.1 — Security and Dependency Audit

**Audit Date:** 2026-11-04  
**Target:** Entire repository, dependency inventory, and cryptographic components  
**Raw Test Evidence:** `audit/v4.0.1-final-independent/raw_evidence/security_scan.log`

---

## 1. Dependency Inventory and Zero-Core-Dependency Policy

The core MDRAP engine in [`pyproject.toml`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/pyproject.toml#L39-L42) declares **zero mandatory external dependencies**:
```toml
dependencies = [
    # MDRAP core platform has zero mandatory external dependencies (pure Python stdlib).
]
```
Optional components for analytics, UI, and export pin bounded versions:
- `fastapi >= 0.110.0`
- `uvicorn >= 0.28.0`
- `rich >= 13.0.0`
- `pyarrow >= 20.0.0` (Pinned to mitigate known CVEs in PyArrow < 14.0.1 / Arrow RCE CVE-2023-47248)
- `duckdb >= 0.10.0`

### Explicit Scope & Boundary Bounding
This security audit constitutes an internal engineering review covering:
1. Architectural security review of API and storage components.
2. Static regex secret and credential scanning across all codebase assets.
3. Deserialization security inspection (enforcing standard JSON and binary structs; zero `pickle` / `eval` usage).
4. Static dependency version boundary verification against known high-severity CVEs (e.g. PyArrow CVE-2023-47248).

**Boundary Disclaimer:** This audit was executed in an offline air-gapped test environment without live network access to commercial third-party vulnerability databases (e.g., `pip-audit`, Snyk, SonarQube). It does NOT constitute a certified third-party penetration test or an exhaustive CVE database certification.

---

## 2. Hardcoded Secret & Credential Scanning

A full static regex scan across all production sources (`.py`, `.c`, `.h`, `.yaml`, `.toml`, `.json`, `.bat`) was executed via [`security_audit.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/audit/v4.0.1-final-independent/reproduction_scripts/security_audit.py).

- **API Keys / Token Patterns:** 0 findings in production code.
- **Private Key Headers:** 0 findings.
- **Passwords:** 0 findings.
- **Verdict:** **CLEAN.** Authentication tokens in `config.yaml` default to empty strings or environment variable lookups (`MDRAP_DAEMON_TOKEN`).

---

## 3. Unsafe Deserialization Audit

All event serialization and state persistence across `Engine`, `IngestLog`, and `SQLiteProjection` use canonical JSON (`json.dumps` / `json.loads`) or fixed binary structs (`struct.pack` / `struct.unpack`):
- `pickle` is **NOT** used in any ingest, WAL, snapshot, or projection code path.
- `eval()` and `exec()` are **NOT** used on untrusted input.
- `yaml.safe_load()` is strictly enforced in config loading.

---

## 4. Native Extension Supply-Chain & Memory Boundaries

- **C Source Code:** `src/mdrap/fastpath.c` is written in standard C99/C11 without third-party C dependencies.
- **Buffer Overflow Protection:**
  - `FastSlot` size is strictly statically asserted at compile-time: `_Static_assert(sizeof(FastSlot) == 384, "FastSlot layout 6 cachelines");`.
  - Array bounds on `stale_seen` use bitwise circular indexing on 32-entry power-of-2 capacity: `sl->stale_seen[sl->stale_head & 31] = seq;`.
  - Max sources and instruments are clamped to compiled constants (`MAX_SOURCES=32`, `MAX_INSTRUMENTS=1024`).
- **Memory Safety:** Native memory allocations are zero-initialized with `calloc()` and bounded. Native SHM buffers use 0600 POSIX permissions.
