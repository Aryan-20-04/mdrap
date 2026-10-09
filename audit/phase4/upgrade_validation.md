# Phase 4 Upgrade Validation & Rolling Migration Runbook

**Scope**: In-place and rolling cluster upgrades from MDRAP v2.x to v3.0.0  
**Timestamp**: 2026-10-09  

---

## 1. Rolling Upgrade Topology (Active-Passive Pair)

In a Profile B deployment (Primary Node A, Standby Node B):
1. **Upgrade Standby First (Node B)**:
   - Drain local consumer connections on Node B.
   - Stop `mdrap` service on Node B.
   - Update packages (`pip install -U mdrap-3.0.0-py3-none-any.whl`).
   - Run configuration check: `python -m mdrap.cli config check`.
   - Start Node B in `STANDBY` mode.
   - Verify Node B syncs heartbeats and tracks sequence numbers from Node A.
2. **Perform Managed Failover**:
   - Promote Node B to `PRIMARY` via CLI:
     ```bash
     python -m mdrap.cli failover promote --node-id node-b --reason "rolling_upgrade"
     ```
   - Node B steps epoch from $E$ to $E+1$ with updated fencing token.
   - Ingress streams transfer to Node B.
3. **Upgrade Former Primary (Node A)**:
   - Stop `mdrap` service on Node A.
   - Update software packages to v3.0.0.
   - Start Node A in `STANDBY` role with epoch $E+1$.
   - Verify cluster reaches steady state with Node B as Primary and Node A as Standby.
