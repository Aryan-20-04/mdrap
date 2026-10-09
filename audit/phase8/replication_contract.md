# MDRAP Phase 8 — Log Replication & Recovery Contract

## 1. Replication Architecture
In the primary-backup topology:
1. **Authoritative WAL**: The primary node appends normalized, validated events to its partition binary journal (`IngestLog`).
2. **Replication Stream**: Secondary/backup nodes stream WAL chunks or replayed sequence frames asynchronously over the local network via `ReplayBuffer` or dedicated replication sockets.
3. **CRC32 Checksum Validation**: Every replicated frame includes a CRC32 integrity checksum. Any torn or corrupted frame halts replication and triggers resynchronization.

## 2. Recovery on Failover
When a primary fails:
1. The secondary acquires leadership with $\text{Epoch}_{k+1}$.
2. The secondary inspects its local journal's highest sequence number:
   $$\text{HeadSeq}_{\text{secondary}}$$
3. If an upstream message bus or shared journal exists, the new leader replays uncommitted frames to ensure zero sequence gaps.
4. Downstream clients reconnecting to the new leader receive an updated generation token and can initiate gap replay from `HeadSeq` via `SUB ... REPLAY` protocol commands.
