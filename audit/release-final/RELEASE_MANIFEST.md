# MDRAP v6.0.0 Release Manifest

**Release Date**: 2026-11-11  
**Release Tag**: `v6.0.0`  
**Git Source Commit**: `f7734b2081ba136ad37c6b260b89a01bb68f64bd`  
**Target Architecture**: Market Data Reliability & Assurance Platform (Sidecar)  
**Distribution Directory**: `dist/`  

---

## 1. Verified Release Artifacts

| Artifact Name | Format | Size (Bytes) | SHA-256 Checksum |
| :--- | :--- | :--- | :--- |
| `mdrap_core-6.0.0-py3-none-any.whl` | Wheel (`zip`) | 766,039 | `C6F140C5318C4DDF1A910DA24A76978B8870EC3B7F418FFBD1D8B781F729FD87` |
| `mdrap_core-6.0.0.tar.gz` | Source Distribution (`tar.gz`) | 715,487 | `97934723A4F031C38DA329D2138B323EE65C307389C4D7E4986A5FD929E1F898` |

---

## 2. Release Gate Verification Summary

| Gate Parameter | Required Invariant | Observed Empirical Evidence | Verdict |
| :--- | :--- | :--- | :--- |
| **Active Git Branch** | Clean, synchronized tracking remote | `main` fast-forwarded to `f7734b2`; pushed to `origin` | **PASSED** |
| **Working Tree** | Zero uncommitted modifications | Clean working tree | **PASSED** |
| **Original Release Tags** | Unaltered, historical tags preserved | `v5.3.0`, `v4.0.1` preserved; `v6.0.0` tagged | **PASSED** |
| **Test Collection** | Zero collection errors | 1,718 collected items; 0 collection errors | **PASSED** |
| **Test Execution** | Zero failures across selected suite | 1,677 passed, 1 skipped, 40 deselected, 0 failed, 0 errors | **PASSED** |
| **Test Machine Record** | XML test proof generated | Saved at `audit/release-final/pytest_results.xml` | **PASSED** |
| **Read-Only Boundary** | Zero order routing execution calls | AST scan of 121 modules confirms 0 execution/trading calls | **PASSED** |
| **Zero Core Dependencies**| Pure Python stdlib baseline | `pyproject.toml` dependencies = `[]`; stdlib only | **PASSED** |
| **Native C Parity** | Pure Python fallback matches C kernel | 100% semantic agreement on all 15 quality rules | **PASSED** |
| **External Installation** | Functional installation outside repo | Verified in `$env:TEMP` with native and fallback modes | **PASSED** |
| **Customer Demo** | 12-step operational demonstration | Automated execution completed in 8.2s without error | **PASSED** |

---

## 3. Dependency Specifications

### Core Engine (Zero Mandatory Dependencies)
- Python `>= 3.10` standard library only (`sqlite3`, `hashlib`, `zlib`, `struct`, `ctypes`, `threading`, `json`).

### Optional Component Extras
- `mdrap-core[api]`: `fastapi>=0.110.0`, `uvicorn>=0.28.0`, `httpx>=0.27.0`
- `mdrap-core[ui]`: `rich>=13.0.0`
- `mdrap-core[feeds]`: `websockets>=12.0,<17.0`
- `mdrap-core[export]`: `pyarrow>=20.0.0`
- `mdrap-core[columnar]`: `duckdb>=0.9.0`, `pyarrow>=20.0.0`
- `mdrap-core[yaml]`: `pyyaml>=6.0`
- `mdrap-core[kafka]`: `confluent-kafka>=2.3.0`
- `mdrap-core[all]`: Installs all optional extras for full feature coverage.

---

## 4. Public API & Compatibility Changes

1. **Namespace Normalization**: All modules are canonical under `mdrap.*`. Root-level shims (`src/*.py`) have been eliminated.
2. **Pruned Out-of-Scope Modules**:
   - `mdrap.navigator` (Interactive modal terminal) -> Pruned.
   - `mdrap.trading_cli` (Retail news scraping, corporate actions) -> Pruned.
   - `mdrap.af_xdp` (Unimplemented Linux kernel bypass stubs) -> Pruned.
   - `mdrap.fx` (Retail currency conversion) -> Pruned.
3. **Deprecation Shims Retained**:
   - `mdrap.vessel`: Raises `CompanionPackageMissing` pointing to `packages/mdrap-contrib-vessel`.
   - `mdrap.options`: Raises `CompanionPackageMissing` pointing to `packages/mdrap-options`.
