# MDRAP 6.0.0 — Comprehensive Dependency & Vulnerability Audit

**Audit Date:** November 2026  
**Auditor:** Principal Cybersecurity Researcher & Release Engineer  
**Scope:** `pyproject.toml`, `requirements.txt`, Subprojects, and Transitive Dependencies  

---

## 1. Executive Summary

This audit assesses the declared dependencies across all `pyproject.toml` manifests in the repository for known CVEs, version obsolescence, licensing conflicts, and runtime deprecations.

---

## 2. Declared Dependency Manifest Audit

### Root `pyproject.toml`
```toml
[project]
name = "mdrap-core"
version = "6.0.0"
dependencies = [] # Zero mandatory core dependencies

[project.optional-dependencies]
cli = ["rich>=13.0.0"]
api = ["fastapi>=0.100.0", "uvicorn>=0.22.0", "websockets>=11.0"]
crypto = ["cryptography>=41.0.0"]
all = ["rich>=13.0.0", "fastapi>=0.100.0", "uvicorn>=0.22.0", "websockets>=11.0", "cryptography>=41.0.0"]
```

### Subproject Manifests
- `compat/pyproject.toml`: Declares version `6.0.0`, depends on `mdrap-core==6.0.0`.
- `contrib/pyproject.toml`: Declares version `6.0.0`, depends on `mdrap-core==6.0.0`.

---

## 3. Runtime Deprecations & Transitive Findings

During the 1,745-test suite run, 18 deprecation warnings were captured in `audit/v6.0.0/raw/pytest_results.json`:

1. **`websockets.legacy` Deprecation in Uvicorn:**
   ```
   DeprecationWarning: websockets.legacy is deprecated; see https://websockets.readthedocs.io/en/stable/howto/upgrade.html
   from websockets.server import WebSocketServerProtocol
   ```
   - **Root Cause:** Uvicorn's WebSocket protocol handler imports `websockets.legacy` which is deprecated in `websockets >= 14.0`.
   - **Action:** Pin `websockets>=11.0,<14.0` in `optional-dependencies` until Uvicorn releases a modernized protocol handler.
2. **`PytestRemovedIn10Warning` in AnyIO Plugin:**
   ```
   PytestRemovedIn10Warning: _pytest.python.CallSpec2 has been renamed to CallSpec.
   ```
   - Test harness warning; does not affect production runtime.

---

## 4. Software License Compliance

| Dependency | License | Permissible in Commercial / HFT? | Notes |
| :--- | :--- | :--- | :--- |
| `rich` | MIT | **YES** | Clean permissive license |
| `fastapi` | MIT | **YES** | Clean permissive license |
| `uvicorn` | BSD-3-Clause | **YES** | Clean permissive license |
| `websockets`| BSD-3-Clause | **YES** | Clean permissive license |
| `cryptography`| Apache 2.0 / BSD | **YES** | Dual licensed |

Zero copyleft (GPL / AGPL) dependencies detected. All optional libraries comply with institutional commercial policies.
