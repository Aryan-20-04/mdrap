# MDRAP Phase 6 — Multi-Tenant Architecture & Resource Partitioning

## 1. Executive Summary & Scope Definition
In an institutional market data context, "multi-tenancy" refers to multiple internal business units (e.g., Algorithmic Trading Desks, Quantitative Research Groups, Real-Time Risk Engines, and Regulatory Audit Ledgers) concurrently sharing access to MDRAP streams without cross-tenant interference or data contamination.

MDRAP Phase 6 implements **Logical Multi-Tenancy with Resource Quotas** (`TenantQuotaManager` in `src/partition.py`), avoiding the heavy operational cost of deploying physically isolated clusters for every internal desk.

---

## 2. Multi-Tenant Architectural Model

```
                                 [ MDRAP Distribution Kernel ]
                                               │
                                               ▼
                                  ┌──────────────────────────┐
                                  │   TenantQuotaManager     │
                                  └──────┬────────────┬──────┘
                                         │            │
                  ┌──────────────────────┘            └──────────────────────┐
                  ▼                                                          ▼
      ┌──────────────────────────────┐                           ┌──────────────────────────────┐
      │     TENANT ALPHA (ALGO)      │                           │      TENANT BETA (RISK)      │
      ├──────────────────────────────┤                           ├──────────────────────────────┤
      │  - Quota: 500 symbols        │                           │  - Quota: 50 symbols         │
      │  - Rate Limit: 10,000 eps    │                           │  - Rate Limit: 1,000 eps     │
      │  - Queue: Dedicated Bounded  │                           │  - Queue: Dedicated Bounded  │
      │  - Billing: Non-Display Algo │                           │  - Billing: Non-Display Risk │
      └──────────────────────────────┘                           └──────────────────────────────┘
```

---

## 3. Tenant Boundary Enforcement

### 3.1 Entitlement-Driven Subscription Filtering
- When a tenant establishes a connection, its credentials map to a `ClientEntitlement` record (`src/security.py`).
- The tenant may only subscribe to symbols and feed venues explicitly permitted by its entitlement manifest.
- Unauthorized symbol requests are rejected with `403 Forbidden` or dropped silently during wire filtering.

### 3.2 Subscription Quota Enforcement
- Prevents a single rogue script from subscribing to the entire 50,000-symbol options universe.
- If `requested_symbol_count > tenant.max_subscriptions`, subscription request is rejected.

### 3.3 Dynamic Rate Limiting (Token Bucket)
- Each tenant is bounded by a sliding-window rate limit (e.g., standard users capped at 5,000 eps; VIP desks capped at 25,000 eps).
- If a consumer generates excessive queries or requests, excess traffic is throttled without degrading peer tenants.
