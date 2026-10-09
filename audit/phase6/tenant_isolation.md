# MDRAP Phase 6 — Multi-Tenant Resource Isolation & Quota Controls

## 1. Executive Summary & Design Scope
In an institutional environment, MDRAP services multiple internal algorithmic trading desks and analytics consumers within a shared deployment. Multi-tenant isolation ensures that no tenant can monopolize system bandwidth, trigger memory exhaustion, or inspect unentitled market data streams.

---

## 2. Multi-Tenant Isolation Layers

| Layer | Isolation Mechanism | Implementation in Code | Failure Action |
| :--- | :--- | :--- | :--- |
| **Authentication** | PBKDF2/SHA-256 salted token hash | `src/security.py` | Connection refused (`401 Unauthorized`) |
| **Subscription Quota** | Max allowed symbol subscriptions | `TenantQuotaManager.check_subscription_permitted()` | Request rejected (`403 Forbidden`) |
| **Rate Limiting** | Sliding-window token bucket rate limit | `TenantQuotaManager.record_and_check_rate()` | Excess traffic throttled (< 2.5 µs check) |
| **Noisy Neighbor** | Dedicated bounded fan-out queue | `ConsumerFanoutManager.broadcast_event()` | Slow client auto-evicted after 10 drops |
| **Billing Accounting** | Per-tenant tick metering & Merkle hash | `src/metering.py` | Signed EOD exchange usage manifest |

---

## 3. Mathematical Quota Enforcement

- **Subscription Ceilings**:
  Standard Desk Quota = 50 symbols. VIP Desk Quota = 500 symbols.
- **Sliding-Window Rate Limiter**:
  Maintains rolling 1-second timestamp deque per tenant. Checks:
  $$\text{WindowRate} = \sum_{t \in [T - 1.0, T]} \text{Events}(t) \le \text{MaxRate}$$
  If breached, excess packets are dropped without affecting peer tenants.
