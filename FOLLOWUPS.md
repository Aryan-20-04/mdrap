# MDRAP Architecture & Engineering Follow-Ups Ledger

This file tracks non-blocking, out-of-scope, and future architectural items extracted from historical audits and reviews, in strict compliance with the Phase 0 audit reconciliation and followups policy.

| ID | Discovered Phase | File / Subsystem | Finding / Item | Risk | Recommended Phase / Milestone |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **F-01** | Phase 0 (Audit Recon) | `src/mdrap/sbe.py`, `src/mdrap/itch.py` | Additional protocol decoders (e.g. full FIX 5.0 SP2 edge messages, specialized venue ITCH variants) | Scope creep prior to core storage and ingestlog stabilization | Post-v3.0.0 Ecosystem Expansion |
| **F-02** | Phase 0 (Audit Recon) | `src/mdrap/storage.py` | External analytics sinks (PostgreSQL, ClickHouse) beyond SQLite projection and DuckDB columnar | Dilutes single-writer durability focus on SQLite/IngestLog | Post-v3.0.0 Platform Sink Expansion |
| **F-03** | Phase 0 (Audit Recon) | `Dockerfile`, `docker-compose.yml` | Kubernetes/container hardening (read-only rootfs, fine-grained capability drops, seccomp profiles) | Operational security outside single-node server scope | v3.1.0 Cloud & Orchestration Hardening |
| **F-04** | Phase 0 (Audit Recon) | Hardware / Kernel | FPGA / hardware kernel offload (Solarflare OpenOnload, kernel bypass EF_VI) | Premature optimization before software pipeline saturation | Future Hardware Acceleration Research |
| **F-05** | Phase 0 (Audit Recon) | `src/mdrap/api.py` | Alternative transport interfaces (gRPC streaming, GraphQL subscriptions) | Premature API surface expansion before REST/WS stabilization | v3.2.0 Multi-Protocol Ingest |
| **F-06** | Phase 0 (Audit Recon) | Multi-node Clustering | Distributed multi-process sharding / distributed consensus replication | Unnecessary complexity before single-writer durability is verified | v4.0.0 Distributed Clustering |
| **F-07** | Phase 0 (Audit Recon) | CI / Hardware Runners | Native testing on physical AWS Graviton (aarch64) and macOS Apple Silicon | Latent ARM memory barrier/seqlock subtleties if CI runs only on emulators | CI Infrastructure Upgrade Milestone |
| **F-08** | Phase 0 (Audit Recon) | Observability / Telemetry | Centralized Grafana dashboards and OpenTelemetry distributed tracing exports | Redundant until unified single metric registry and log offsets settle | v3.1.0 Observability Expansion |
| **F-09** | Phase 0 (Audit Recon) | Soak & Reliability | Multi-week (> 7 days) live exchange continuous soak testing | Long runtime cannot be gated in standard CI/CD release cycles | Continuous Production Soak Campaign |
| **F-10** | Phase 0 (Audit Recon) | Community & Packaging | Third-party plugin registry marketplace and package signing repository | Ecosystem overhead prior to stable core conformance kits | Ecosystem & Community Milestone |
