# MDRAP Phase 3 — SDK Packaging and Distribution Policy

**Document Identifier**: `MDRAP-DIST-P3-001`  
**Date**: October 9, 2026  

---

## 1. Artifact Distribution Strategy

To comply with the institutional rule against unauthenticated external publishing:

1. **C++ Consumer SDK**:
   - Header-only core + compiled static library (`libmdrap_consumer.a` / `mdrap_consumer.lib`).
   - Packaged locally in `dist/sdk/cpp/include` and `dist/sdk/cpp/lib`.
   - CMake configuration file provided (`MDRAPConsumerConfig.cmake`).

2. **Java Consumer SDK**:
   - Packaged as local JAR: `mdrap-consumer-3.1.0.jar`.
   - Maven POM provided in `sdk/java/pom.xml` for local enterprise Nexus / Artifactory hosting.

3. **Rust Consumer SDK**:
   - Source crate located in `sdk/rust/`.
   - Consumed via Git dependency (`git = "https://internal-vcs/mdrap.git", path = "sdk/rust"`) or private crates registry.

---

## 2. Security and Checksum Verification

All distributed binaries and source archives are hashed with SHA-256 before staging.
No external network registries (e.g. Maven Central, crates.io, PyPI) are contacted during automated local builds.
