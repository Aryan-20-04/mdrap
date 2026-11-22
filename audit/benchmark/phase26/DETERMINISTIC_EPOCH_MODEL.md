# MDRAP Phase 2.6 — Deterministic Epoch & Sequence Identity Model

## 1. Problem Statement & Motivation

In high-throughput institutional market-data networks, simple integer sequence counters without explicit session boundaries are vulnerable to three critical failure modes:
1. **Out-of-Order Genesis Packets**: Packet 0 arriving after packet 50 triggers false reset heuristics and subsequent false gap alerts.
2. **Epoch Boundary Bleed**: In network failover scenarios, delayed packets from an old TCP connection or multicast buffer arriving after a session restart trigger massive false forward gaps.
3. **Multi-Gateway Interleaving**: Redundant feed handlers (e.g. primary and secondary exchange gateways) for the same venue alternate sequences, corrupting a shared monotonic counter.

To solve these problems permanently, MDRAP implements a formal **5-Dimensional Deterministic Sequence Identity Model**.

---

## 2. Five-Dimensional Identity Model

Every market data update is evaluated within a strictly scoped identity tuple:

$$\text{Identity Tuple} = \langle \mathcal{V}, \mathcal{S}, \mathcal{G}, \mathcal{E}, \mathcal{N} \rangle$$

Where:
- $\mathcal{V}$ (**Venue**): Source exchange or liquidity pool (e.g., `BINANCE`, `KRAKEN`, `COINBASE`).
- $\mathcal{S}$ (**Symbol**): Instrument identifier (e.g., `BTC-USDT`, `ETH-USD`).
- $\mathcal{G}$ (**Gateway / Channel**): Originating network gateway or feed channel (e.g., `gw-primary`, `gw-secondary`, `depth_channel_1`).
- $\mathcal{E}$ (**Epoch Identifier**): Discrete connection session or matching-engine epoch (e.g., integer `1, 2, ...`, session UUID, or ISO date-time string).
- $\mathcal{N}$ (**Sequence Number**): Exchange-native monotonically increasing integer sequence.

### Channel Scoping Rule
Within `VenueHealth`, state is partitioned into isolated channel trackers:
$$\text{Channel Key} = \mathcal{G} \mathbin{\Vert} \text{":"} \mathbin{\Vert} \mathcal{S}$$
Events arriving from different gateways or for different instruments never cross-contaminate or overwrite each other's sequence baselines.

---

## 3. Epoch State Machine (`ChannelSequenceState`)

### 3.1 State Transitions

```
                    ┌─────────────────────────┐
                    │       UNINITIALIZED     │
                    │   (last_feed_seq = -1)  │
                    └────────────┬────────────┘
                                 │ Initial event
                                 ▼
                    ┌─────────────────────────┐
                    │         ACTIVE          │◄──────────────────────┐
                    │   (last_feed_seq = seq) │                       │
                    └────────────┬────────────┘                       │
                                 │                                    │
        ┌────────────────────────┼────────────────────────┐           │
        │ seq == last + 1        │ seq > last + 1         │ is_snapshot│
        ▼                        ▼                        ▼           │
 ┌─────────────┐          ┌─────────────┐          ┌──────────────┐   │
 │ CONTINUOUS  │          │ FORWARD GAP │          │ SNAPSHOT     │───┘
 │ last = seq  │          │ gaps += Δ   │          │ last = seq   │
 └─────────────┘          │ last = seq  │          │ snapshots++  │
                          └─────────────┘          └──────────────┘
        │                        │
        │ epoch < current        │ epoch > current
        ▼                        ▼
 ┌─────────────┐          ┌─────────────┐
 │ STALE EPOCH │          │ EPOCH RESET │
 │ stale_pkts++│          │ resets++    │
 │ (DISCARDED) │          │ last = seq  │
 └─────────────┘          └─────────────┘
```

### 3.2 Formal State Transition Table

| Current State | Event Condition | Action Taken | Resulting Diagnostic | Next State |
| :--- | :--- | :--- | :--- | :--- |
| Any | `seq is None` | Ignore for sequence tracking; do not alter baseline. | `feed_seq_unavailable += 1` | Unchanged |
| Any | `is_snapshot is True` | Re-anchor sequence baseline: `last_feed_seq = seq`. Zero gap penalty. | `feed_snapshots += 1` | `HEALTHY_SNAPSHOT` |
| Active ($\mathcal{E}$) | $\mathcal{E}_{\text{event}} < \mathcal{E}_{\text{current}}$ | **Discard event from continuity tracking**. Do NOT advance `last_feed_seq`. Zero gap penalty. | `stale_epoch_packets += 1` | Unchanged |
| Active ($\mathcal{E}$) | $\mathcal{E}_{\text{event}} > \mathcal{E}_{\text{current}}$ | **Explicit Epoch Transition**. Update `current_epoch_id = epoch_id`. Re-anchor `last_feed_seq = seq`. Zero gap penalty. | `feed_resets += 1` | `HEALTHY_EPOCH_TRANSITION` |
| Active ($\mathcal{E}$) | $\mathcal{E}_{\text{event}} == \mathcal{E}_{\text{current}} \land seq == last + 1$ | Continuous in-order update. Advance `last_feed_seq = seq`. | None | `HEALTHY` |
| Active ($\mathcal{E}$) | $\mathcal{E}_{\text{event}} == \mathcal{E}_{\text{current}} \land seq > last + 1$ | Genuine forward gap. Increment `feed_gaps += (seq - last - 1)`. Advance `last_feed_seq = seq`. | `feed_gaps += Δ` | `HEALTHY` |
| Active ($\mathcal{E}$) | $\mathcal{E}_{\text{event}} == \mathcal{E}_{\text{current}} \land seq == last$ | Duplicate packet. Do NOT advance `last_feed_seq`. | `feed_duplicates += 1` | `HEALTHY` |
| Active ($\mathcal{E}$) | $\mathcal{E}_{\text{event}} == \mathcal{E}_{\text{current}} \land seq < last$ | **Out-of-order packet within epoch**. Do NOT rewind `last_feed_seq`. Zero gap penalty. | `feed_out_of_order += 1` | `HEALTHY` |
| Active (No Epoch) | $\mathcal{E}_{\text{event}} \text{ is None} \land seq == 0 \land last > 0$ | **Out-of-order genesis packet**. Do NOT rewind `last_feed_seq`. Zero gap penalty. | `feed_out_of_order += 1` | `DEGRADED_UNSCOPED_EPOCH` |
| Active (No Epoch) | $\mathcal{E}_{\text{event}} \text{ is None} \land seq == 1 \land last > 10$ | Unscoped legacy reset heuristic. Re-anchor `last_feed_seq = seq`. | `degraded_resets += 1`, `feed_resets += 1` | `DEGRADED_UNSCOPED_RESET` |

---

## 4. Multi-Gateway Scoping & Diagnostic Counters

When multiple gateways feed the same venue without specifying `gateway_id`:
- The system increments the explicit diagnostic counter `gateway_unscoped += 1`.
- When `epoch_id` is omitted, the system increments `epoch_unavailable += 1` and marks the stream state as `DEGRADED_UNSCOPED_EPOCH`.
- Under no circumstances is an un-scoped, un-epoch-scoped stream fabricated into a clean deterministic session. The diagnostics surface the exact quality limitations of the upstream feed.

---

## 5. Verification Proof Summary

The deterministic epoch model was proven across 15 adversarial tests in `tests/test_phase26_remediation.py`:
- `test_seq_initial_sequence_zero`: Sequence 0 accepted as valid start.
- `test_seq_out_of_order_zero_after_higher_sequence`: Out-of-order 0 increments `feed_out_of_order`, preserves `last_feed_seq`, 0 false gaps.
- `test_seq_delayed_packet_from_previous_epoch`: Stale packet 4999 discarded as `stale_epoch_packets = 1`, 0 false gaps.
- `test_seq_multiple_gateways_independent_sequence_domains`: Gateways A (1000->1001) and B (100->101) tracked in parallel with 0 false gaps.
