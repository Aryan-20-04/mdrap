# MDRAP Phase 3 — Replication Contract

**Document Identifier**: `MDRAP-REPL-P3-001`  
**Date**: October 9, 2026  

---

## 1. Commit and Replication Boundary

1. **Local Durability**: An event is durable on the primary when written to the `IngestLog` WAL and fsynced.
2. **Replication Stream**: The primary streams raw WAL frames to the standby over TCP socket.
3. **Standby Catch-up**: The standby validates CRC32 on every replicated frame before appending to its local replica WAL.
4. **Promotion Guard**: A standby cannot promote to `PRIMARY` while in `SYNCING` state (`local_seq < primary_last_seq`). Promotion is blocked until full sequence catch-up is achieved.
