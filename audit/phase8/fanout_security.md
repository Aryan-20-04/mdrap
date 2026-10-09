# MDRAP Phase 8 — Fan-Out Security & Tenant Isolation

## 1. Authentication & Pre-Dispatch Entitlement
Before a client can register with `AsyncFanoutManager`, security gates are enforced at the transport and session layer:

1. **Authentication Token**: Clients authenticate via SHA-256 HMAC hashed API keys (`SecurityManager`).
2. **Symbol Entitlement Filtering**: Sessions define allowed symbol universes (e.g. `subscribed_symbols = {"AAPL", "MSFT"}`). In `AsyncFanoutManager`, symbols outside the client's entitlement are filtered out prior to queue insertion:
   ```python
   if s.subscribed_symbols is not None and sym not in s.subscribed_symbols:
       continue
   ```
3. **No Cross-Tenant Memory Leakage**: Each client's deque is completely isolated in memory. A tenant cannot inspect or consume events intended for another tenant.
4. **Denial-of-Service Defense**: Stalled or malicious consumers attempting slow-read attacks are automatically disconnected upon reaching the drop threshold ($\ge 50$ drops).
