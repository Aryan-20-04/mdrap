# Security Policy

## Supported Versions

| Version | Supported          | Security Scope |
| ------- | ------------------ | -------------- |
| 6.0.x   | :white_check_mark: | Active Release (Read-Only Sidecar Architecture) |
| 5.3.x   | :white_check_mark: | Maintenance    |
| 5.x     | :white_check_mark: | Maintenance    |
| 4.x     | :white_check_mark: | Maintenance    |
| < 4.0   | :x:                | End of Life    |

---

## Read-Only Security Invariant

MDRAP is designed from the ground up as a **zero-trust, read-only security boundary**:
1. **Zero Trading Execution Risk**: The codebase contains zero order routing, modification, execution, or cancellation code. It does not store or request private API keys, secrets, or trading passphrases.
2. **Loopback Isolation**: By default, local REST and WebSocket endpoints bind strictly to `127.0.0.1:8000`.
3. **Mandatory Cryptographic Salt**: API key hashing and authentication routines require an explicit 32-byte cryptographic salt (`MDRAP_API_KEY_SALT`).

---

## Reporting a Vulnerability

The MDRAP maintainers take security and data reliability seriously. If you discover a vulnerability or security-sensitive defect in MDRAP:

1. **Do not open a public GitHub issue.**
2. Send an email with reproduction steps, environment details, and impact to `security@mdrap.org` (or contact the repository maintainers via GitHub Private Vulnerability Reporting).
3. We will acknowledge receipt of your report within 48 hours and provide a timeline for triage and remediation.
4. Security advisories and patched releases will be published through GitHub Security Advisories.
