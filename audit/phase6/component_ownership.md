# MDRAP Phase 6 — Component Ownership & Architecture Governance

## 1. Executive Summary & Purpose
Clear ownership prevents neglected code paths and ensures that changes to critical modules undergo expert peer review before merging. This matrix maps all repository modules to their primary and secondary engineering owners.

---

## 2. Component Ownership Matrix

| Repository Subsystem | Core Files | Primary Engineering Owner | Secondary / Reviewer |
| :--- | :--- | :--- | :--- |
| **Core Ingestion & WAL** | `src/gateway.py`, `src/ingestlog.py` | Principal Core Systems Architect | Distributed Systems Lead |
| **Native C Fastpath** | `src/fastpath.c`, `src/mdrap_core.c` | Senior Systems Engineer | Core Architect |
| **Partitioning & Fan-Out** | `src/partition.py` | Distributed Systems Engineer | SRE Lead |
| **Quality & Reconciler** | `src/quality.py`, `src/reconciliation.py`| Quantitative Data Architect | Lead Systems Architect |
| **SBE & Wire Protocols** | `src/sbe.py`, `src/gateway_tcp.py` | Senior Network Systems Engineer | Client SDK Lead |
| **Security & RBAC** | `src/security.py` | Principal Security Architect | DevOps Lead |
| **Usage Metering & EOD** | `src/metering.py` | Billing & Compliance Engineer | Quantitative Lead |
| **SRE, Telemetry & Tools** | `src/prometheus.py`, `scripts/` | Principal SRE Lead | DevOps Engineer |
