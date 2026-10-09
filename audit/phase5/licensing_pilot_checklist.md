# MDRAP Phase 5 — Licensing and Entitlement Pilot Onboarding Checklist

## 1. Purpose & Pre-Conditions
This checklist must be executed and approved prior to granting any downstream consumer (internal trading desk, quantitative research group, or external institutional client) network access to MDRAP canonical SBE, SHM, or WebSocket market data feeds.

---

## 2. Onboarding Verification Checklist

### Section A: Legal and Exchange Agreement Verification
- [x] **Exchange Redistribution Agreement**: Verified that the consumer's entity is covered by the institution's master redistribution contract for the requested venue feeds (e.g., NASDAQ Vendor Agreement).
- [x] **Usage Classification Declaration**: Consumer has formally declared intended usage category:
  - [x] `NON_DISPLAY_TRADING` (Automated execution algorithms)
  - [ ] `NON_DISPLAY_RISK` (Real-time risk calculations)
  - [ ] `DISPLAY_TERMINAL` (Human trading UI)
- [x] **Unit-of-Count Identification**: Assigned unique `client_id` adhering to exchange naming specifications (e.g., `DESK_ALPHA_ALGO`).

### Section B: Technical Entitlement Provisioning
- [x] **Access Role Configuration**: Entitlement record generated with appropriate role:
  - `Role.CONSUMER` (Read-only access to SBE stream and REST snapshot endpoints).
- [x] **Venue & Symbol Filtering**: Configured permitted venues (e.g., `venues=["NASDAQ", "BATS"]`) and symbol whitelist.
- [x] **Cryptographic Token Generation**: API secret generated via `secrets.token_urlsafe(32)`:
  - Secret transmitted out-of-band via secure vault.
  - Stored in MDRAP database as PBKDF2/SHA-256 salted hash (`token_hash`).
  - Derived 64-bit `key_id` assigned for instant unambiguous revocation.

### Section C: Accounting & Telemetry Setup
- [x] **Metering Registry Registration**: Tenant registered in the in-memory atomic counter table (`usage_metering`).
- [x] **Prometheus Tenant Metric Labeling**: Confirmed `mdrap_metered_ticks_total{client_id="DESK_ALPHA_ALGO"}` actively exposed.
- [x] **EOD Report Mapping**: Configured tenant's automated inclusion in the daily exchange reporting batch job.

### Section D: Consumer Sandbox Conformance
- [x] **Socket Handshake Verification**: Verified consumer correctly executes initial authentication frame within 1,000 ms of socket connection.
- [x] **Heartbeat Conformance**: Consumer acknowledges 5-second heartbeats (`0x00` frame) without timing out.
- [x] **Sequence Gap Handling**: Consumer demonstrates automated handling of sequence numbers and gap detection.

### Section E: Revocation Drill Validation
- [x] **Instant Revocation Test**: Executed `python cli.py security revoke --key-id <id>` in sandbox:
  - Consumer socket immediately closed by server within 50 ms.
  - Reconnection attempts return `401 Unauthorized`.
  - Disconnect event recorded in compliance audit log.

---

## 3. Pilot Sign-Off Authorization

- **Client ID**: `DESK_ALPHA_PILOT`
- **Assigned IP Subnet**: `10.200.4.0/24`
- **Authorized Venues**: `NASDAQ`, `BATS`
- **Compliance Officer**: Market Data Compliance Manager (APPROVED)
- **Technical Approver**: Principal SRE / Network Security (APPROVED)
- **Effective Activation Date**: `2026-10-09`
