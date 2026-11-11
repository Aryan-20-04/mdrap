# MDRAP 6.0.0 — REMEDIATION REPORT: AGENT 5

## WORKSTREAM: WEBSOCKET BROADCAST SECURITY & TOKEN CACHING
**Finding Remediated**: `MDRAP-SEC-01`  
**Engineer / Lead**: Agent 5 — Security & Cryptographic Systems Specialist  
**Date**: 2026-11-12  
**Branch**: `remediation/v6.0.0-p1-hardening`  
**Status**: **VERIFIED FIXED**

---

### 1. Original Defect Description
In `src/mdrap/api.py:370-380`, `broadcast_event()` executed synchronous token lookups on every connected client for every broadcasted event:
```python
if ws in self.subscriber_tokens:
    tok = self.subscriber_tokens[ws]
    ent = (
        self.security_manager.get_entitlement(tok, active_only=True)
        if tok
        else None
    )
```
In `security.py:546`, `get_entitlement` re-computed salted HMAC-SHA256 candidate hashes (`self._hash_candidates(key)`) and performed timing-safe string comparisons (`hmac.compare_digest`). Under realistic institutional market rates (10,000 ticks/sec across 100 subscribers), this resulted in 1,000,000 HMAC computations per second directly inside the broadcast loop, completely starving the asyncio event loop and causing severe latency spikes, client drops, and denial of service.

---

### 2. Implementation Details & Files Changed

#### Modified: [`src/mdrap/api.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/mdrap/api.py)
1. **Entitlement Caching**:
   - Added `self.subscriber_entitlements: Dict[WebSocket, Any] = {}` to `AppState`.
   - In `websocket_events_stream()`, cached the authenticated `client_ent` immediately after connection authorization:
     ```python
     st.subscriber_entitlements[websocket] = client_ent
     ```
2. **O(1) Hot-Path Verification in `broadcast_event()`**:
   - Replaced repeated HMAC hashing with an $O(1)$ attribute check:
     ```python
     ent = self.subscriber_entitlements.get(ws)
     if ent is not None:
         if not ent.is_active or (ent.expires_at is not None and now > ent.expires_at):
             dead_sockets.append(ws)
             continue
     ```
3. **Immediate Revocation Propagation**:
   - Because `security_manager.revoke_api_key()` directly sets `ent.is_active = False` on the shared `ClientEntitlement` instance, revoked keys are instantly detected on the very next event broadcast without delay or token re-hashing.
4. **Lifecycle Cleanup**:
   - Pruning dead sockets purges entries from `self.subscriber_entitlements`, preventing memory leaks.

---

### 3. Regression & Verification Tests

#### Created: [`tests/test_broadcast_security.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/tests/test_broadcast_security.py)
1. `test_broadcast_cached_entitlement_avoids_hmac_dos`:
   - Mocks and wraps `get_entitlement` and `_hash_candidates`.
   - Broadcasts 20 market events and asserts that neither method is invoked during broadcast (0 calls).
2. `test_immediate_eviction_on_key_revocation`:
   - Authenticates a subscriber, verifies clean message delivery, then revokes the API key via `revoke_api_key(token)`.
   - Asserts `ent.is_active` transitions to `False` and the WebSocket connection is immediately closed on the subsequent broadcast.

---

### 4. Execution Verification Evidence
```
tests/test_broadcast_security.py::test_broadcast_cached_entitlement_avoids_hmac_dos PASSED [ 50%]
tests/test_broadcast_security.py::test_immediate_eviction_on_key_revocation PASSED [100%]

======================== 2 passed, 1 warning in 1.25s =========================
```

---

### 5. Remaining Limitations
Entitlement caching is local to the active process. In a distributed multi-node deployment, key revocation events must be broadcast over a message bus (e.g. Redis pub/sub) to invalidate node-local cached entitlements.
