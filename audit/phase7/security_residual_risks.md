# MDRAP Phase 7 — Security Residual Risk Register & Threat Mitigations

## 1. Executive Summary & Assessment
Following comprehensive threat modeling, static analysis, dependency scanning, and adversarial testing, this register records all remaining **Residual Security Risks** alongside their likelihood, impact, and operational mitigating controls.

---

## 2. Residual Security Risk Register

| Risk ID | Threat Description | Likelihood | Impact | Severity | Mitigating Controls & Operational Guardrails |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **SEC-RES-01** | **Local OS Shared Memory Snooping** (Unprivileged local user reading `/dev/shm` buffers) | Low | High | Medium | Linux permissions set strictly to `chmod 0600`; Windows DACLs restrict access to SYSTEM and MDRAP service account. |
| **SEC-RES-02** | **Compromised Staged Archive Extraction** (Malicious replacement of archived WAL segment on S3) | Very Low | High | Medium | All archived segments require cryptographic verification against signed SHA-256 Merkle root hashes in `archive_manifest.json`. |
| **SEC-RES-03** | **High-Frequency Ingress DoS via Public WebSocket Feeds** | Medium | Medium | Medium | Ingress gateway enforces bounded input queues; unauthenticated frames dropped; rate limiting throttles rogue connections. |
| **SEC-RES-04** | **Host Time Desynchronization / NTP Slew** | Low | High | Medium | Engine validates clock source (`EXCHANGE` vs `GATEWAY_RECV`); alerts emitted if receive timestamp deviates $> 1000\text{ ms}$ from system wall-clock. |
| **SEC-RES-05** | **Third-Party Zero-Day in Python Standard Library** | Very Low | Critical | Low | Operating system and Python runtime patches monitored continuously; container base images pinned to official LTS digests. |

---

## 3. Operational Security Recommendations
1. Run MDRAP processes under a dedicated, unprivileged service account (`mdrap-svc`) with zero sudo or root privileges.
2. Store production API signing secrets in hardware security modules (HSM) or dedicated enterprise secret vaults (HashiCorp Vault / AWS Secrets Manager).
3. Enforce strict firewall rules restricting TCP ports $9002–9005$ to authorized internal algorithmic trading subnets.
