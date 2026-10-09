# MDRAP Phase 3 — Fencing and Split-Brain Prevention

**Document Identifier**: `MDRAP-FENCE-P3-001`  
**Date**: October 9, 2026  

---

## 1. Fencing Token Mechanics

Split-brain occurs when a network partition causes two nodes to believe they are the authoritative primary simultaneously. MDRAP prevents dual-primary publication through monotonic epoch fencing:

1. **Epoch Progression**: Promotion strictly increments `epoch` and updates `fencing_token = epoch`.
2. **Writer Assertion**: All storage append operations invoke `assert_fencing_token(token)`. If `token < active_token`, the writer is rejected with `StaleEpochError`.
3. **Partition Resolution**: When two nodes assert `PRIMARY` at the same epoch, a deterministic tie-breaker resolves the conflict: the node with lexicographically lower `node_id` remains `PRIMARY`, while the higher `node_id` immediately yields to `STANDBY`.
