# MDRAP Phase 6 — Privileged Operations & Administrative Governance

## 1. Executive Summary & Policy Scope
Administrative commands executed against the MDRAP control plane—such as draining shards, terminating consumer connections, revoking API tokens, or altering partition boundaries—possess significant operational blast radius. This document establishes the authorization criteria, security gates, and audit trails required for all privileged platform actions.

---

## 2. Privileged Action Inventory

| Operation | CLI Command | Minimum Required Role | Confirmation Required | Audit Log Category |
| :--- | :--- | :--- | :--- | :--- |
| **Shard Drain** | `python cli.py fleet drain --shard <id>` | `Role.ADMIN` | Yes (`--confirm`) | `FLEET_LIFECYCLE` |
| **Consumer Eviction** | `python cli.py fleet evict-consumer` | `Role.ADMIN` | Yes (`--reason`) | `CONSUMER_ACCESS` |
| **API Key Revocation** | `python cli.py security revoke --key-id` | `Role.ADMIN` | No (Immediate) | `SECURITY_REVOKE` |
| **Legal Hold Freezing**| `python cli.py compliance legal-hold` | `Role.ADMIN` | Yes (`--case-id`) | `COMPLIANCE_HOLD` |
| **Storage Compaction** | `python cli.py storage checkpoint` | `Role.ADMIN` | No | `STORAGE_MAINT` |

---

## 3. Authorization and Execution Flow

```
[ Operator Invocates CLI ] ──> [ Check Token Validity & Role.ADMIN ]
                                             │
                                             ├─(Unauthorized)─> [ Emit 403 Forbidden & Alert ]
                                             │
                                             ▼ (Authorized)
                               [ Verify Mandatory Flags (--confirm / --reason) ]
                                             │
                                             ▼
                               [ Execute Surgical Shard Action ]
                                             │
                                             ▼
                               [ Write Immutable HMAC Record to control.log ]
```

---

## 4. Audit Log Non-Repudiation Invariant

Every administrative action writes an append-only JSON record to `/var/data/mdrap/audit/control.log`:
- Mandatory fields: `timestamp_utc`, `operator_id`, `client_ip`, `action`, `target_resource`, `arguments_redacted`, `status`.
- Filesystem permissions on `control.log` are locked to mode `0600`, preventing tampering by unprivileged users.
