"""
MDRAP v5.2 Phase 3: Matching Engine, Queue Position & Adverse Selection Audit.

Empirically evaluates:
1. Touch-to-Fill Gap (Price Touched vs Order Executed).
2. Queue-Ahead Volume Decay and Fill Delay.
3. Cancellation vs Fill Races under varying network latencies.
4. Multi-Horizon Adverse Selection Markouts (10ms, 100ms, 1s, 5s, 30s).
5. Exports RAW_MATCHING_TELEMETRY.json.
"""

from __future__ import annotations

import json
import logging
import math
import random
import sys
import time
from pathlib import Path
from typing import Any

# Ensure paths
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "packages" / "mdrap-strategies"))

from mdrap.models import CanonicalEvent, EventType
from mdrap.simulator import FeedSimulator, SimulatorConfig
from mdrap_strategies.matching_engine import (
    RealisticMatchingEngine,
    MatchingEngineConfig,
    SimulatedOrder,
    SimOrderSide,
    SimOrderType,
    SimOrderStatus,
    FeeSchedule,
)


def run_queue_position_experiments(events: list[CanonicalEvent]) -> dict[str, Any]:
    """
    Evaluates order fill probability and time-to-fill as a function of initial queue-ahead depth.
    """
    queue_depths = [0.0, 50.0, 150.0, 500.0, 1500.0]
    results = {}

    for q_depth in queue_depths:
        config = MatchingEngineConfig(network_latency_ms=1.0, matching_delay_ms=0.5)
        engine = RealisticMatchingEngine(config=config)
        
        submitted_orders = 0
        filled_orders = 0
        total_touches = 0
        fill_latencies_ms = []

        # Replay events
        current_time = 1700000000.0
        active_order_id = None
        order_submit_ts = 0.0

        for i, ev in enumerate(events):
            current_time += 0.010  # 10ms per tick
            px = ev.price or 100.0

            # Update book
            engine.update_book(
                symbol="BTCUSDT",
                bids=[(px - 0.05, q_depth), (px - 0.10, q_depth * 2)],
                asks=[(px + 0.05, q_depth), (px + 0.10, q_depth * 2)],
                timestamp=current_time,
            )

            # Check existing order
            if active_order_id is not None:
                ord_obj = engine.orders[active_order_id]
                if ord_obj.status == SimOrderStatus.FILLED:
                    filled_orders += 1
                    fill_latencies_ms.append((ord_obj.fill_events[0]["timestamp"] - order_submit_ts) * 1000.0)
                    active_order_id = None
                elif current_time - order_submit_ts > 5.0: # 5 second timeout
                    engine.cancel_order(active_order_id, current_time=current_time)
                    active_order_id = None

            # Submit limit order at current bid if none active
            if active_order_id is None and i < len(events) - 100:
                active_order_id = f"Q_ORD_{q_depth}_{i}"
                order_submit_ts = current_time
                engine.submit_order(
                    order_id=active_order_id,
                    symbol="BTCUSDT",
                    side=SimOrderSide.BUY,
                    order_type=SimOrderType.LIMIT,
                    quantity=10.0,
                    price=px - 0.05,
                    current_time=current_time,
                )
                submitted_orders += 1

            # Process market trade
            engine.process_market_trade(
                symbol="BTCUSDT",
                trade_price=px - 0.05 if (i % 3 == 0) else px,
                trade_size=25.0,
                timestamp=current_time,
            )

        fill_rate = (filled_orders / max(1, submitted_orders)) * 100.0
        avg_fill_lat = (sum(fill_latencies_ms) / len(fill_latencies_ms)) if fill_latencies_ms else 0.0

        results[f"depth_{int(q_depth)}"] = {
            "queue_ahead_volume": q_depth,
            "orders_submitted": submitted_orders,
            "orders_filled": filled_orders,
            "fill_rate_pct": round(fill_rate, 2),
            "avg_fill_latency_ms": round(avg_fill_lat, 2),
        }

    return results


def run_cancellation_race_experiments(events: list[CanonicalEvent]) -> dict[str, Any]:
    """
    Evaluates cancellation success rate vs fill in race conditions across latencies.
    """
    latencies = [0.2, 1.0, 5.0, 10.0, 25.0] # in ms
    results = {}

    for lat in latencies:
        config = MatchingEngineConfig(network_latency_ms=lat, matching_delay_ms=0.1)
        engine = RealisticMatchingEngine(config=config)

        races_conducted = 0
        cancels_won = 0
        fills_suffered = 0

        current_time = 1700000000.0
        for i in range(200):
            current_time += 0.050
            order_id = f"RACE_{lat}_{i}"
            
            # Post resting order
            engine.update_book("BTCUSDT", bids=[(100.0, 10.0)], asks=[(100.1, 10.0)], timestamp=current_time)
            ord_obj = engine.submit_order(
                order_id=order_id,
                symbol="BTCUSDT",
                side=SimOrderSide.BUY,
                order_type=SimOrderType.LIMIT,
                quantity=10.0,
                price=100.0,
                current_time=current_time,
            )
            
            # Fast forward time to when order is accepted
            current_time += (lat + 0.1) / 1000.0
            engine.process_time_slice(current_time)

            # Strategy decides to cancel because toxic trade is detected
            engine.cancel_order(order_id, current_time=current_time)
            
            # Aggressive toxic trade arrives at exchange after 2.0ms
            trade_arrival = current_time + 0.002 # 2ms after cancel was sent
            engine.process_market_trade(
                symbol="BTCUSDT",
                trade_price=100.0,
                trade_size=50.0,
                timestamp=trade_arrival,
            )
            
            # Advance past cancel arrival
            current_time += (lat / 1000.0) + 0.005
            engine.process_time_slice(current_time)

            races_conducted += 1
            if ord_obj.status == SimOrderStatus.CANCELLED:
                cancels_won += 1
            elif ord_obj.status in (SimOrderStatus.FILLED, SimOrderStatus.PARTIALLY_FILLED):
                fills_suffered += 1

        cancel_win_pct = (cancels_won / races_conducted) * 100.0
        results[f"lat_{lat}ms"] = {
            "network_latency_ms": lat,
            "races_conducted": races_conducted,
            "cancels_won": cancels_won,
            "fills_suffered": fills_suffered,
            "cancel_success_rate_pct": round(cancel_win_pct, 2),
            "fill_leakage_pct": round(100.0 - cancel_win_pct, 2),
        }

    return results


def run_adverse_selection_multi_horizon(events: list[CanonicalEvent]) -> dict[str, Any]:
    """
    Computes post-trade markouts at 10ms, 100ms, 1s, 5s, and 30s.
    """
    config = MatchingEngineConfig(network_latency_ms=1.0, matching_delay_ms=0.5)
    engine = RealisticMatchingEngine(config=config)
    
    current_time = 1700000000.0
    horizons_ms = [10.0, 100.0, 1000.0, 5000.0, 30000.0]

    # Prepopulate price history
    for i, ev in enumerate(events):
        current_time += 0.050 # 50ms per tick
        px = ev.price or (100.0 + (i % 20) * 0.02)
        engine.update_book(
            symbol="BTCUSDT",
            bids=[(px - 0.02, 100.0)],
            asks=[(px + 0.02, 100.0)],
            timestamp=current_time,
        )

        # Place periodic market and limit orders to generate fills
        if i % 10 == 0:
            engine.submit_order(
                order_id=f"FILL_ORD_{i}",
                symbol="BTCUSDT",
                side=SimOrderSide.BUY if (i % 20 == 0) else SimOrderSide.SELL,
                order_type=SimOrderType.MARKET,
                quantity=10.0,
                current_time=current_time,
            )
            engine.process_time_slice(current_time + 0.002)

    # Compute markouts for each horizon
    markout_summary = {}
    for h in horizons_ms:
        markouts = engine.compute_adverse_selection_markout(horizon_ms=h)
        if markouts:
            bps_vals = [m["markout_bps"] for m in markouts]
            mean_bps = sum(bps_vals) / len(bps_vals)
            markout_summary[f"{int(h)}ms"] = {
                "trades_analyzed": len(markouts),
                "mean_markout_bps": round(mean_bps, 4),
                "p25_markout_bps": round(sorted(bps_vals)[int(0.25 * len(bps_vals))], 4),
                "p50_markout_bps": round(sorted(bps_vals)[int(0.50 * len(bps_vals))], 4),
                "p75_markout_bps": round(sorted(bps_vals)[int(0.75 * len(bps_vals))], 4),
                "adverse_ratio_pct": round(len([b for b in bps_vals if b < 0]) / len(bps_vals) * 100.0, 2),
            }

    return markout_summary


def main():
    print("=" * 80)
    print("MDRAP v5.2 PHASE 3: EXECUTION SIMULATOR & MICROSTRUCTURE AUDIT")
    print("=" * 80)

    # Generate synthetic event series
    sim_cfg = SimulatorConfig(instruments=["BTCUSDT"], num_events=3000, seed=42)
    sim = FeedSimulator(sim_cfg)
    raw_events = sim.generate()

    canonical_events = []
    for item in sim.generate():
        r = item[0] if isinstance(item, tuple) else item
        px = r.payload.get("price") or (r.payload.get("bid", 100.0) + r.payload.get("ask", 100.1)) / 2.0
        if not isinstance(px, (int, float)):
            px = 100.0
        canonical_events.append(
            CanonicalEvent(
                event_id=r.raw_id,
                instrument_id=str(r.payload.get("instrument", "BTCUSDT")),
                event_type=EventType.TRADE if "price" in r.payload else EventType.QUOTE,
                exchange_timestamp=float(r.payload.get("exchange_ts", r.receive_timestamp)) if isinstance(r.payload.get("exchange_ts"), (int, float)) else r.receive_timestamp,
                receive_timestamp=r.receive_timestamp,
                processing_timestamp=r.receive_timestamp + 0.0001,
                source=r.source,
                sequence_number=int(r.payload.get("sequence", 0)) if str(r.payload.get("sequence", "")).isdigit() else 0,
                price=float(px),
                quantity=float(r.payload.get("quantity", 10.0)) if isinstance(r.payload.get("quantity"), (int, float)) else 10.0,
            )
        )

    print("\n[1/3] Running Queue-Position Depth vs Fill-Rate Experiments...")
    q_results = run_queue_position_experiments(canonical_events)
    for k, v in q_results.items():
        print(f"  -> {k}: Fill Rate = {v['fill_rate_pct']}% | Avg Latency = {v['avg_fill_latency_ms']} ms")

    print("\n[2/3] Running Cancellation vs Fill Latency Race Experiments...")
    race_results = run_cancellation_race_experiments(canonical_events)
    for k, v in race_results.items():
        print(f"  -> {k}: Cancel Success = {v['cancel_success_rate_pct']}% | Fill Leakage = {v['fill_leakage_pct']}%")

    print("\n[3/3] Running Multi-Horizon Adverse Selection Markouts...")
    markout_results = run_adverse_selection_multi_horizon(canonical_events)
    for k, v in markout_results.items():
        print(f"  -> {k}: Mean Markout = {v['mean_markout_bps']} bps | Adverse Ratio = {v['adverse_ratio_pct']}%")

    # Save to JSON
    telemetry = {
        "timestamp": time.time(),
        "queue_position_experiments": q_results,
        "cancellation_race_experiments": race_results,
        "adverse_selection_markouts": markout_results,
    }
    out_path = ROOT / "audit" / "v5.2" / "RAW_MATCHING_TELEMETRY.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(telemetry, f, indent=2)

    print(f"\n[OK] Phase 3 telemetry successfully exported to {out_path}")


if __name__ == "__main__":
    main()
