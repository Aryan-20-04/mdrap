# MDRAP Phase 5 — User & Consumer Access Review Procedure

## 1. Scope & Compliance Mandate
In accordance with FINRA Rule 4511, SEC Rule 17a-4, and SOC 2 Type II criteria, all privileged access credentials and API tokens granting access to MDRAP infrastructure, configuration, and market data streams must undergo rigorous quarterly re-certification and immediate event-driven revocation.

---

## 2. Access Classes and Scopes

| Access Class | Mechanism | Scope / Permissions | Authorized Roles |
| :--- | :--- | :--- | :--- |
| **Host Administrative** | SSH (Ed25519 Keys via Bastion) | Sudo / Host maintenance / Process restart | Certified SRE Engineers only |
| **Engine Management API**| Bearer API Token (`Role.ADMIN`)| Schema reload, hot config changes, key issuance| Lead Systems Architect, SRE Lead |
| **Trading Consumer** | SBE / SHM Token (`Role.CONSUMER`)| Streaming tick subscriptions, venue filtering | Authorized Algorithmic Desks |
| **Observability & Audit** | Read-Only API Token (`Role.VIEWER`)| Prometheus `/metrics`, `/health`, EOD CSV logs | Telemetry scrapers, Compliance Team |

---

## 3. Quarterly Recertification Process

Every 90 days, the Security Officer and Compliance Officer execute the formal recertification:

```
[ Step 1: Export Active Keys ] ──> [ Step 2: Cross-Check HR/Desks ] ──> [ Step 3: Desk Lead Sign-Off ]
                                                                                   │
[ Step 6: File SOC 2 Artifact ] <── [ Step 5: Audit Log Sealing ] <── [ Step 4: Revoke Inactive Keys ]
```

### Step 1: Inventory Generation
Execute the automated key audit script to extract all active entitlements and their last-seen activity timestamps:
```bash
python cli.py security list-keys --active-only --format json > /var/data/mdrap/compliance/active_keys_Q4.json
```

### Step 2: Entitlement Cross-Check
- Cross-reference each `client_id` with active trading desk accounts in the enterprise directory.
- Verify whether the redistribution agreement for each venue is still active and fully paid.
- Flag any token that has exhibited zero socket activity for > 30 consecutive days.

### Step 3: Desk Commander Certification
Send generated reports to desk heads for formal sign-off. Any uncertified token is scheduled for automated deactivation.

### Step 4: Immediate Deactivation & Revocation
Revoke uncertified or dormant keys using the deterministic `key_id`:
```bash
python cli.py security revoke --key-id <key_id>
```

---

## 5. Event-Driven Offboarding and Emergency Deprovisioning

When an employee departs or a trading counterparty is terminated:
1. **SLA**: Key revocation must occur within **15 minutes** of ticket notification.
2. **Execution**: SRE executes `revoke_api_key()` via CLI or REST API.
3. **Verification**: Live socket connection dropped immediately; SBE handshake handler refuses subsequent attempts.
4. **Audit Evidence**: Revocation event and operator identity logged to immutable audit ledger.
