# MDRAP v5.3 — Execution Engine & Microstructure Matching Audit

## Overview
This document evaluates the event-driven execution matching engine (`RealisticMatchingEngine`), order queue models, cancellation race mechanics, fee structures, and latency simulation.

---

## 1. Matching Engine Architecture (`RealisticMatchingEngine`)

### 1.1 Microstructure Realism
Unlike simplistic backtest simulators that assume immediate fills upon price touch, `RealisticMatchingEngine` enforces:
1. **Strict Price-Time Priority**: Per-symbol, per-side, per-price FIFO order queues.
2. **Queue-Ahead Quantity Tracking**:
   When a limit order is submitted, the prevailing resting depth at that price level is recorded as `queue_ahead_qty`. The limit order cannot execute until market volume equal to `queue_ahead_qty` has traded at that level.
3. **Simulated Wire & Matching Latency**:
   Orders undergo scheduled in-flight delays:
   $$t_{arrival} = t_{decision} + \frac{\text{network\_latency\_ms} + \text{matching\_delay\_ms}}{1000}$$
4. **Cancellation Race Conditions**:
   Cancellations are also subject to network transit latency. If an aggressor order hits a resting limit order before the cancel request arrives at the exchange matching engine, the order fills, simulating real-world cancel/fill races.

---

## 2. v5.3 Remediations Implemented & Verified

### 2.1 Duplicate Order Protection
- **Vulnerability**: Previously, duplicate order submissions overwrote existing orders in `self.orders`, corrupting queue positions and double-counting fill allocations.
- **Remediation**: `submit_order` checks `if order_id in self.orders:` and rejects duplicates with `SimOrderStatus.REJECTED` and `reject_reason="DUPLICATE_ORDER_ID"`.

### 2.2 Global Trading Kill Switch
- **Vulnerability**: In emergency market conditions, strategies had no mechanism to halt execution simulator order dispatch.
- **Remediation**: Implemented `activate_kill_switch()`, `deactivate_kill_switch()`, and `cancel_all(symbol)`. All subsequent submissions are immediately rejected with `reject_reason="KILL_SWITCH_ACTIVE"`.

### 2.3 Stale Market Data Rejection
- **Vulnerability**: If an exchange feed froze or disconnected, the simulator continued executing orders against stale book prices.
- **Remediation**: Added `max_stale_data_s` guard. If elapsed time since last `update_book` exceeds the threshold, orders are rejected with `reject_reason="STALE_MARKET_DATA"`.

All remediations are verified by unit tests in `tests/test_v53_quantitative_remediation.py`.
