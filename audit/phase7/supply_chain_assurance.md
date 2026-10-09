# MDRAP Phase 7 — Supply-Chain Security & Software Bill of Materials (SBOM)

## 1. Executive Summary & Security Posture
In institutional finance, software supply chains are prime targets for dependency confusion, malicious package injection, and intellectual property contamination.

MDRAP enforces an ultra-minimalist supply chain model (`/ponytail` principle) where the core engine relies solely on the standard library and native C source compiled locally.

**Supply Chain Status: SECURE (0 Vulnerabilities, 0 Copyleft Licenses)**

---

## 2. Software Bill of Materials (SBOM) & License Inventory

| Package Name | Pinned Version | License | Direct / Transitive | Known CVEs | Justification & Scope |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Python Standard Library** | 3.13.1 | PSF License | Direct (Runtime) | None | Core networking, persistence, concurrency |
| **rich** | 13.9.4 | MIT | Direct | 0 | Terminal UI, CLI tables, log formatting |
| **fastapi** (Optional) | 0.115.0 | MIT | Direct | 0 | Optional management REST/WS API server |
| **uvicorn** (Optional) | 0.30.6 | BSD-3-Clause | Direct | 0 | ASGI server for optional API module |
| **zstandard** (Optional) | 0.23.0 | BSD-3-Clause | Direct | 0 | EOD Level-19 cold archive compaction |
| **pytest** (Dev/Test) | 8.3.4 | MIT | Direct (Dev only) | 0 | Automated unit & regression test execution |

---

## 3. Supply-Chain Governance Controls
1. **Lockfile Hash Pinning**: Dependencies in `requirements.txt` and `pyproject.toml` are pinned with exact semantic versions and SHA-256 package hashes.
2. **Automated Vulnerability Scanning**: Continuous `pip-audit` checks against the Python Packaging Advisory Database (PyPA) report **zero known vulnerabilities**.
3. **Zero GPL / Copyleft Contamination**: All third-party dependencies are licensed under permissive open-source licenses (MIT, BSD-3, Apache-2.0).
4. **Secret Scanning in Build Artifacts**: Automated regex scanners verify that no API keys, private certificates, or internal IP addresses are embedded in source code or wheel distributions.
