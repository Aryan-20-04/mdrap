# MDRAP Commercial Strategy & Edition Boundaries

> **Phase C: Edition Boundaries, Commercial Gate 3, and Open Source Permanence**  
> **Status:** Ratified Strategy Document  
> **Core License:** Permissive MIT License (Frozen)

---

## 1. Executive Summary & Philosophy

The Market Data Reliability & Acceleration Platform (MDRAP) follows an uncompromising architectural and licensing principle:

> **The complete, single-node core engine is 100% Free Open Source Software (MIT) forever.**

MDRAP will **never**:
- Artificially cripple algorithms or validation rules in the Free Edition.
- Impose synthetic event-per-second (EPS) rate limits or memory caps to coerce upgrades.
- Embed telemetry phone-home dials, DRM, or license-key validation routines into the core engine.
- Silently degrade durability, WAL reliability, or crash recovery for non-paying users.

Commercial monetization begins **only after Gate 3 demand validation is satisfied**, and only via value-added operational tooling, enterprise integrations, and guaranteed support contracts packaged strictly outside the core engine.

---

## 2. Edition Capabilities Matrix

| Capability / Subsystem | Free Edition (Permanent) | Future Commercial Edition | Status & Trigger |
|:---|:---:|:---:|:---|
| **Quality Engine (Python & C Tiers)** | Full (100% Rule Parity) | Same (Identical Core) | Shipped & Verified |
| **IngestLog WAL (Segmented, CRC32, Direct I/O)** | Full (Recovery, Verify, Salvage) | Same (Identical Format) | Shipped & Verified |
| **Native C Hot-Path Kernel** | Included (`fastpath.c`) | Same | Shipped & Verified |
| **Lock-Free SHM Ring Buffer & TCP Fan-out** | Included (Single-Node) | Same | Shipped & Verified |
| **CLI, Simulator & Doctor Diagnostics** | Included (`mdrap` CLI) | Same | Shipped & Verified |
| **Local API Key Auth & RBAC** | Included (Viewer/Operator/Admin) | Same | Shipped & Verified |
| **Metrics, Health & Prometheus Exporters** | Included ($O(1)$ Scrape) | Same | Shipped & Verified |
| **Fault-Injection & Universal Test Harness** | Included (Full Suite) | Same | Shipped & Verified |
| **Paid Support, Onboarding & SLA** | Community / GitHub Issues | Dedicated SLA (4h response) | **Trigger:** First paying request |
| **Disaster Recovery (Offsite S3/GCS Shipping)** | Manual / Scripted Backup | Automated Daemon Replicator | **Trigger:** Post-Gate 3 user request |
| **Multi-Node Fleet Console & Policy Push** | Not Included | Central Management UI | **Trigger:** $\ge 3$ users with multi-node fleets |
| **Enterprise SSO / SAML / OIDC** | Local Salted Hash Tokens | Okta / Azure AD / Keycloak | **Trigger:** Enterprise procurement mandate |
| **Regulatory Audit & Compliance Reports** | Merkle Audit Trail in SQLite | Signed RTS 25 / FINRA Export | **Trigger:** Concrete regulator inquiry |
| **High Availability & Distributed Clustering** | In-Process Simulation Only | External Raft / etcd Coordinator | **Strict Non-Goal** on current engine |

---

## 3. Strict Non-Goals & Integrity Disclosures

### 3.1 Distributed Consensus & High Availability
- **Disclosure:** The `ConsensusCoordinator` class in `src/mdrap/consensus.py` is an **in-process simulation** for local failure-scenario testing. Two independent daemon instances started on separate ports will both elect themselves leader.
- **Policy:** MDRAP **must never be marketed or sold as a distributed HA clustering solution** based on the current in-process coordinator.
- **Future Architecture:** If multi-node active-passive failover is demanded by commercial clients, leadership election will be delegated completely to an external, industry-proven distributed coordinator (e.g., etcd, ZooKeeper, or Consul), with the MDRAP daemon acting purely as an acquire-lease follower.

### 3.2 Regulatory Compliance Disclaimers
- MDRAP provides nanosecond-precision timestamps, monotonic sequence counters, and SHA-256 Merkle provenance logs.
- However, MDRAP will **never** claim to be "SEC Rule 613 / MiFID II RTS 25 Compliant" out of the box. Compliance is a property of the firm's entire operating infrastructure, time synchronization hardware (PTP/IEEE 1588), and operational governance, not a software library.

---

## 4. Architectural Separation Rules for Commercial Extensions

To preserve open-source integrity, all commercial offerings must abide by the following architectural isolation boundaries:

1. **Clean MIT Core:**
   The core repository (`mdrap`) contains zero proprietary code, zero license check gates, and zero commercial upsell banners in the CLI or logs.
2. **Sidecar Packaging:**
   Commercial features (e.g. `mdrap-enterprise`, `mdrap-dr`) reside in separate Python packages and repositories. They interact with MDRAP strictly via:
   - Public Python APIs (`Engine.subscribe()`, `IngestLog.iter_from()`).
   - The frozen on-disk WAL format (v2 framing with CRC32).
   - Standard Prometheus metrics and REST health endpoints.
3. **Universal Test Equivalence:**
   The entire open-source test suite must run and pass against both the core open-source package and the commercial extensions.
4. **Synchronized Security Fixes:**
   All security patches, memory safety fixes, and data-integrity remediations ship simultaneously to both open-source and commercial distributions.

---

## 5. Gate 3 Commercial Pilot Criteria

The project will **not** commit engineering resources to building proprietary commercial software until all of the following conditions are met:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        GATE 3 DECISION CRITERIA                        │
├────────────────────────────────────────────────────────────────────────┤
│ 1. [ ] ≥ 3 independent quant/fintech teams successfully complete a     │
│        1-day Shadow-Mode PoC using scripts/shadow_poc.py.              │
│                                                                        │
│ 2. [ ] Shadow-Mode PoC confirms:                                       │
│        - Defect Recall ≥ 95% (catches real sequence gaps / crossed quotes)│
│        - False Positive Rate ≤ 2.0%                                    │
│        - Zero unhandled crash-recovery data loss.                      │
│                                                                        │
│ 3. [ ] At least 1 qualified team explicitly requests commercial SLA    │
│        onboarding, custom venue adapter development, or enterprise DR. │
│                                                                        │
│ 4. [ ] Invalidation check passes: Customer confirms that existing      │
│        in-house scripts cannot match MDRAP's reliability/TCO ratio.    │
└────────────────────────────────────────────────────────────────────────┘
```

If Gate 3 is satisfied, commercial development begins with **Commercial SLA Onboarding & Incident Response Services** before any proprietary code is written.
