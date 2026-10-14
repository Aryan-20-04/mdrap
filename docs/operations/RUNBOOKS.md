# MDRAP Production Operational Runbooks

**Scope**: Standard Operating Procedures (SOPs) for site reliability engineers (SRE) and quantitative infrastructure operators running MDRAP.

---

## Runbook 1: Feed Outage & Watchdog Silence Alert

### Symptoms
- Prometheus metric `mdrap_feed_status{source="<FEED>"}` falls to `0`.
- Watchdog logs alert: `[watchdog] Silence alert on source <FEED>: last seen X.Xs ago`.
- Downstream strategies observe consensus switching to alternate source.

### Diagnostic Steps
1. Query live feed status via CLI:
   ```bash
   mdrap status
   ```
2. Verify venue socket connectivity and network route:
   ```bash
   mdrap doctor
   ```
3. Inspect whether outage is market-session expected (e.g. market halt or scheduled break) vs unexpected network failure.

### Mitigation
- If feed socket is dead, the gateway worker attempts automatic exponential backoff reconnection.
- If primary feed remains down, consensus automatically selects the highest-reliability active competing feed (Rule 10).
- If feed was declared `DEAD` and requires manual re-enablement:
  ```bash
  mdrap feed reset --source <FEED>
  ```

---

## Runbook 2: Torn-Tail IngestLog WAL Recovery

### Symptoms
- Engine crashes on restart with `TornTailDetectedWarning` or CRC failure on uncompacted segment tail after power failure or hard SIGKILL.
- Startup log: `[ingestlog] Torn frame detected at offset X; salvaging uncorrupted prefix`.

### Diagnostic Steps
1. Run automated segment verification:
   ```bash
   mdrap verify --wal data/wal/
   ```
2. Inspect damaged segment index and byte offset.

### Remediation
1. Salvage cleanly written prefix by truncating the torn trailing frame:
   ```bash
   mdrap verify --wal data/wal/ --salvage --backup
   ```
2. Rebuild downstream SQLite and DuckDB projections from pristine WAL prefix:
   ```bash
   mdrap replay --wal data/wal/ --rebuild-projections
   ```
3. Verify zero sequence gaps post-recovery:
   ```bash
   mdrap verify --wal data/wal/
   ```

---

## Runbook 3: Zero-Downtime API Key Rotation

### Objective
Rotate an active client API key without dropping WebSocket or TCP market data streaming sessions.

### Procedure
1. Generate new active API key with target client entitlement role:
   ```bash
   mdrap keys create --client-id "Strategy_Alpha" --role TRADER
   ```
   Note the newly returned token: `mdrap_live_XXXXXXXX...`.
2. Distribute the new key to client configuration (both keys remain simultaneously valid during rotation window).
3. Verify client reconnects using the new key:
   ```bash
   mdrap status --clients
   ```
4. Revoke legacy compromised or expiring key using key ID:
   ```bash
   mdrap keys revoke --key-id <OLD_KEY_ID>
   ```
5. Confirm old key rejection:
   ```bash
   mdrap keys list
   ```

---

## Runbook 4: Slow-Consumer Detection & Client Eviction

### Symptoms
- Log message: `[service] Client buffer overflow for <IP:PORT>: dropped_ticks >= max_dropped_ticks (1000)`.
- Client queue reaches max capacity; socket is cleanly disconnected to protect publisher.

### Diagnostic Steps
1. Inspect per-client session lag:
   ```bash
   mdrap status --clients
   ```
2. Check client network bandwidth or consumer CPU throttling.

### Remediation
1. Ensure consumer uses dedicated socket reading thread with socket option `TCP_NODELAY`.
2. For quote-only consumers, enable conflation mode or switch to zero-copy shared memory (`shm` transport) if co-located.
3. For sequenced trades, consumer should reconnect and issue `REPLAY from_seq to_seq` to backfill dropped frames.

---

## Runbook 5: ARM Deployment & Weak Memory Ordering Guard

### Architecture Note
ARM64 (aarch64) features a weakly-ordered memory model. Pure-Python `shm.py` ring buffers fail-closed on ARM without native C hardware memory barriers (`dmb ishld` / `dmb ishst`).

### Deployment Requirements
1. Always compile and verify the Native C hot-path kernel on ARM64:
   ```bash
   python build_fastpath.py
   ```
2. Verify native fastpath presence:
   ```bash
   python -c "from mdrap.fastpath import is_available; assert is_available()"
   ```
3. Set environment variable to enable hardware barrier support:
   ```bash
   export MDRAP_SHM_FORCE_ARM="1"
   ```
