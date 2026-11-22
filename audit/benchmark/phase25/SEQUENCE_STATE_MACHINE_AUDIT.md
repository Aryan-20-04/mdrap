# MDRAP Phase 2.5 — Sequence State Machine & Reconnect Heuristic Audit

## 1. Executive Summary & Objective

The Phase 2.4 remediation introduced sequence tracking in `external_sidecar/mdrap_observer/observer/state.py` using the reconnect heuristic:
```python
if seq <= 1 and self.last_feed_seq > 10:
    self.feed_resets += 1
else:
    self.feed_out_of_order += 1
```
The goal of this audit is to adversarial stress-test this heuristic against 10 concrete real-world market-data transport scenarios, determine what the state machine can and cannot distinguish, and formulate institutional recommendations for production readiness.

All 10 scenarios were implemented and validated in:
`external_sidecar/mdrap_observer/tests/test_sequence_heuristic_attack_phase25.py`

---

## 2. Adversarial Scenario Analysis

| Scenario ID | Operational Scenario | Input Sequence | State Machine Behavior | Observed Metrics | Audit Classification |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **SCEN-01** | Legitimate Reset | $5000 \to 1$ | `seq <= 1 and last > 10` evaluates `True`. | `feed_resets = 1`, `feed_gaps = 0`, `feed_out_of_order = 0`. | **PASS (Intended Behavior)** |
| **SCEN-02** | Duplicate Sequence 1 | $1 \to 1$ | `seq == last` branch triggers. | `feed_duplicates = 1`, `feed_resets = 0`. | **PASS (Intended Behavior)** |
| **SCEN-03** | Out-of-Order Sequence 0 | $50 \to 0 \to 51$ | Packet 0 triggers reset heuristic; sets `last=0`. Next packet 51 sees $\Delta = 50$. | `feed_resets = 1`, `feed_out_of_order = 0`, `feed_gaps = 50`. | **FAIL (Heuristic Flaw)** |
| **SCEN-04** | Snapshot Beginning at 1 | $5000 \to 1$ (snapshot) | `is_snapshot=True` explicitly handles snapshot; if unflagged, falls back to reset. | Flagged: `snapshots=1, resets=0`. Unflagged: `resets=1`. | **PASS WITH QUALIFICATION** |
| **SCEN-05** | Reconnect Without Reset | $5000 \to 5021$ | Missed packets during reconnect; next packet sequence continues. | `feed_gaps = 20`, `feed_resets = 0`. | **PASS (Gap Accurately Counted)** |
| **SCEN-06** | Reset Without Reconnect | $120000 \to 1$ | Daily exchange rollover on active connection. | `feed_resets = 1`, `feed_gaps = 0`. | **AMBIGUOUS (Cannot Distinguish)** |
| **SCEN-07** | Delayed Packet from Old Epoch | $5000 \to 1 \to 2 \to 3 \to 4999$ | Delayed packet 4999 from old epoch arrives after reset. $4999 > 3+1$ triggers forward gap. | `feed_gaps = 4995` (massive false gap), `last=4999`. | **FAIL (Fatal Heuristic Flaw)** |
| **SCEN-08** | Interleaved Gateways (A & B) | $1000 \to 100 \to 1001$ | Gateway A at 1000, B at 100. Alternates between out-of-order and massive gap. | `feed_out_of_order = 1`, `feed_gaps = 900`. | **FAIL (Fatal Heuristic Flaw)** |
| **SCEN-09** | Venue Reset + Continuous Transport | Transport: $1 \to 2$, Venue: $5000 \to 1$ | Decoupled domains track independently. | `stream.gaps = 0`, `venue.resets = 1`. | **PASS (Decoupled Domains)** |
| **SCEN-10** | Transport Drop + Continuous Venue | Transport: $10 \to 50$, Venue: $1000 \to 1001$ | Transport detects drop; venue detects continuity. | `stream.gaps = 39`, `venue.gaps = 0`. | **PASS (Decoupled Domains)** |

---

## 3. In-Depth Failure Mode Analysis

### Failure Mode 1: Out-of-Order Sequence 0 (Scenario 3)
- **Mechanism**: In protocols where sequence 0 is valid (e.g. initial book state or SBE reset), an out-of-order packet with `seq = 0` arriving while `last_feed_seq = 50` matches `seq <= 1 and last > 10`.
- **Consequence**:
  1. The packet is incorrectly recorded as `feed_resets += 1` instead of `feed_out_of_order += 1`.
  2. `last_feed_seq` is overwritten with `0`.
  3. When the next expected packet (`seq = 51`) arrives, the state machine computes a false forward gap of $51 - 0 - 1 = 50$ events!

### Failure Mode 2: Delayed Packets from Previous Connection Epoch (Scenario 7)
- **Mechanism**: If a network glitch triggers a reconnect and reset to sequence 1, and subsequently a delayed packet from the old connection (e.g. sequence 4999) arrives over the wire.
- **Consequence**:
  The state machine evaluates `4999 > last_feed_seq + 1`, interpreting the delayed packet as a massive forward jump, registering a false gap of **4,995 events**.

### Failure Mode 3: Interleaved Multi-Gateway Delivery (Scenario 8)
- **Mechanism**: In redundant architectures where two active gateway processes feed the sidecar for the same venue without channel or gateway scoping, their sequences interleave.
- **Consequence**:
  The state machine alternates between out-of-order increments and massive false gap increments (e.g. 900 false gaps in Scenario 8).

---

## 4. State Machine Classification & Architectural Recommendations

### 4.1 Classification: Operational Heuristic
The current rule `seq <= 1 and last > 10` is an **operational heuristic**, not a deterministic protocol state machine. It is suitable only for:
- Single-producer, single-consumer configurations.
- Feeds where delayed old-epoch packets are guaranteed to be dropped at the transport/socket layer before application delivery.
- Feeds that do not deliver out-of-order sequence 0.

### 4.2 Production Institutional Recommendations
To transition from an operational heuristic to an institutional deterministic state machine, future releases must adopt:
1. **Explicit Connection Epoch / Session Token (`epoch_id`)**:
   - Each gateway session or connection must carry an `epoch_id` (e.g., connection UUID or millisecond timestamp).
   - Sequences are evaluated strictly within their `(venue, symbol, epoch_id)` tuple.
   - Any packet arriving from an older `epoch_id` is classified as `delayed_stale_epoch` and discarded without affecting the current sequence baseline.
2. **Mandatory Snapshot Delimitation (`is_snapshot`)**:
   - Upstream order book snapshots must explicitly set `is_snapshot: True`.
   - The state machine must only re-anchor sequence baselines on explicit snapshot frames, never on sequence heuristics.
3. **Gateway Channel Scoping**:
   - Redundant feeds must be scoped by `(venue, channel_id, gateway_id)` to prevent cross-gateway interleaving false alarms.
