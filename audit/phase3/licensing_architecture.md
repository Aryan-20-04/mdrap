# MDRAP Phase 3 — Licensing & Entitlements Architecture

**Document Identifier**: `MDRAP-LIC-P3-001`  
**Date**: October 9, 2026  
**Status**: APPROVED & IMPLEMENTED  

---

## 1. Entitlement Entity Hierarchy

MDRAP decouples commercial contract semantics from operational runtime distribution:

```
[Tenant / Organization]
       │
       ▼ (1:N)
[Client Application / Account]
       │
       ▼ (1:N API Keys)
[ClientEntitlement]
  ├─ is_active (bool)
  ├─ role (VIEWER / OPERATOR / ADMIN)
  ├─ expires_at (epoch seconds)
  ├─ allowed_sources (list[str]) e.g. ["NASDAQ", "CME"]
  ├─ allowed_symbols (list[str]) e.g. ["AAPL", "ES"]
  └─ allowed_cidrs (list[str])
```

---

## 2. Authorization Enforcement Invariants

1. **Fail-Closed Default**: In the absence of an explicit active entitlement record, all stream consumption and query access is rejected.
2. **Clock Expiry**: If `system_time > expires_at`, access is rejected immediately without grace periods unless an explicit grace configuration is enabled.
3. **Venue & Symbol Sandboxing**: Clients with constrained source or symbol lists are blocked from subscribing to non-entitled market streams.
4. **Revocation Immediacy**: Modifying `is_active=False` takes effect immediately across all worker sessions and active WebSocket streams.
