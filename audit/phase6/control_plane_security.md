# MDRAP Phase 6 — Control Plane Security & Access Governance

## 1. Executive Summary & Policy Scope
Administrative operations executed against the fleet control plane—such as draining shards, terminating consumer connections, or adjusting partition boundaries—possess significant blast radius. This document formalizes the security controls, authentication gates, and audit trails required for all control plane interactions.

---

## 2. Control Plane Security Architecture

```
[ Operator / SRE CLI ] ──(Bearer Token: Role.ADMIN)──> [ Management API / IPC Socket ]
                                                                      │
                                                       (Verify Salted PBKDF2 Token)
                                                                      ▼
                                                       [ Check Role == Role.ADMIN ]
                                                                      │
                                                       (Permitted: Log Action)
                                                                      ▼
                                                       [ Append to control.log (HMAC-SHA256) ]
```

### Mandatory Security Controls:
1. **Zero Unauthenticated Endpoints**: Every control plane route mandates an authenticated HTTP Bearer token or local Unix domain credential.
2. **Explicit Target Selection**: Commands must explicitly identify the target shard (`--shard 0`). Wildcard broad commands are barred.
3. **Immutable Audit Trail**: All administrative executions write an immutable record to `/var/data/mdrap/audit/control.log` with operator identity, source IP, timestamp, and action parameters.
