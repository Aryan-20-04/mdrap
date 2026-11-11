# MDRAP Pilot Readiness Audit — Adversarial Review & Skeptical Challenge

**Agent Role:** Independent Adversarial Reviewer & Red Team Auditor  
**Target:** Entire MDRAP Pilot Candidate Architecture (`product-focus/mdrap-reliability-platform`)  
**Mission:** Aggressively challenge claims, expose unverified assumptions, and stress-test failure boundaries  
**Date:** 2026-11-11  

---

## 1. Adversarial Challenge Matrix

| # | System Claim | Skeptical Challenge | Empirical Ground Truth Verdict |
| :- | :--- | :--- | :--- |
| **1** | *"Complete event provenance is preserved for forensic audit."* | If the process terminates or restarts, can an operator retrieve the raw payload SHA-256 and lineage hash? | **FAILED (PRE-REMEDIATION)**. Provenance was kept in an in-memory dictionary. Post-restart queries returned `None` or fell back to legacy lineage which had empty payload SHA-256 and missing channel/data_mode fields. |
| **2** | *"Deterministic incident replay works seamlessly from the CLI."* | Can a trader type `mdrap replay --incident <ID>` immediately after seeing an incident in `mdrap status`? | **FAILED (PRE-REMEDIATION)**. Command failed with `Incident not found in data/mdrap_replay.db` because parser defaulted to the wrong database path. |
| **3** | *"MDRAP is production-ready on Windows institutional desktops."* | Does SQLite file management handle Windows file locking correctly? | **FAILED (PRE-REMEDIATION)**. `IncidentManager` used `with sqlite3.connect(...)` without explicit `conn.close()`, causing leaked file handles and `WinError 32` permission crashes. |
| **4** | *"MDRAP cryptographically proves exchange fault to counterparty trading desks."* | Does an MDRAP Merkle proof prove that Coinbase or Binance authored a malformed quote? | **PARTIALLY VERIFIED / BOUNDARY LIMITED**. MDRAP proves what it received at its ingress. Without upstream exchange cryptographic signatures on wire frames, MDRAP cannot prove exchange authorship if an external MITM occurred prior to TLS termination. |
| **5** | *"Feed freshness monitoring reliably detects dead feeds."* | Does the 10,000 ms threshold work for all asset classes? | **FAILED (PRE-REMEDIATION)**. Hardcoded 10s threshold falsely marks low-frequency instruments as STALE and is too slow for 100ms ultra-HFT channels. |

---

## 2. In-Depth Failure Dissections

### Dissection 1: The "Durable In-Memory" Illusion
The strategic reset introduced `EventProvenance` dataclass with high-quality cryptographic fields (`compute_payload_hash`, `lineage_hash`). However, the implementation recorded them into:
```python
self._records: dict[str, EventProvenance] = {}
```
Because no `event_provenance` table was added to SQLite and `record()` never wrote to disk, any crash or server reboot vaporized all event provenance evidence. Calling `export_evidence()` after reboot produced an incomplete, corrupted record.

### Dissection 2: The CLI UX Disconnect
In `cli status`, active incidents are displayed from `data/mdrap.db`. The operator is invited to inspect incidents. When running `mdrap replay --incident inc-...`, the CLI rejected the incident because `p_replay` defaulted to `data/mdrap_replay.db`. This would immediately frustrate an external pilot customer.

### Dissection 3: Windows Handle Leaks
In Linux/macOS, unclosed file descriptors often delete cleanly upon unlink. On Windows, open SQLite handles cause immediate `PermissionError: [WinError 32] The process cannot access the file because it is being used by another process`. The absence of `conn.close()` in `IncidentManager` violated institutional enterprise desktop standards.

---

## 3. Adversarial Recommendation

**DO NOT CERTIFY PILOT READINESS UNTIL:**
1. A durable `event_provenance` SQLite table is created, and `ProvenanceTracker` persists and retrieves complete forensic records across process restarts.
2. `IncidentManager` explicitly closes all SQLite connection objects (`try ... finally: conn.close()`).
3. `cmd_replay` gracefully resolves `data/mdrap.db` when `--db` is omitted.
4. `WebSocketFeedManager` allows configuring `stale_timeout_ms`.
5. An end-to-end test proves evidence survival and replay after an actual process restart.
