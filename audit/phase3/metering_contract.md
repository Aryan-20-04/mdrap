# MDRAP Phase 3 — Metering & Accounting Contract

**Document Identifier**: `MDRAP-METER-P3-001`  
**Date**: October 9, 2026  

---

## 1. Billable Units Definition

The platform meters three distinct operational units:
1. `DISTRIBUTED_EVENT`: Normalized canonical event successfully transmitted across a consumer socket, WebSocket, or IPC queue.
2. `INGESTED_EVENT`: Raw vendor message accepted, parsed, and committed to the IngestLog WAL.
3. `SUBSCRIPTION_TICK`: Active instrument subscription slice per minute/hour.

---

## 2. Durability & Idempotency Rules

1. **Storage Engine**: SQLite in WAL mode (`synchronous=NORMAL`) with strict schemas.
2. **Idempotency Key**: Every metering batch requires a deterministic unique idempotency key (e.g. `client_session_batch_N`). Duplicate insertions are ignored via SQLite unique constraints, preventing double-billing.
3. **Restart Invariant**: Records written before an engine crash survive process restarts and remain reconcilable.
