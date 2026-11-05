"""
MDRAP v5.1.1 Quantitative Strategy Evaluation & Multi-Baseline Benchmark.

Compares:
1. Avellaneda-Stoikov Institutional Market Maker (AS-MM)
2. Whale Lee-Ready Momentum Strategy (Whale-Momentum)
3. Baseline 1: Buy-and-Hold
4. Baseline 2: No-Trade
5. Baseline 3: Simple Market Maker (symmetric quotes, no inventory skew)
6. Baseline 4: Randomized Signal (random trade entries)

Across:
- In-Sample (IS: 60% of timeline)
- Out-of-Sample (OOS: 40% of timeline)
- Comprehensive ExecutionTearSheet (Gross/Net PnL, Maker/Taker Fees, Sharpe, Sortino, Drawdown, Adverse Selection)
- Multi-Worker Concurrency Scaling Benchmark (1, 2, 4, 8 workers)
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import multiprocessing
import os
import random
import sys
import time
from pathlib import Path
from typing import Any

# Suppress verbose rejection logs during simulation runs
logging.getLogger("mdrap.risk_engine").setLevel(logging.WARNING)

# Ensure paths
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "packages" / "mdrap-strategies"))

from mdrap.models import CanonicalEvent, EventType, QualityStatus
from mdrap.simulator import FeedSimulator, SimulatorConfig
from mdrap.fastpath import FastQualityEngine
from mdrap.gateway import normalize, SchemaError

from mdrap_strategies.matching_engine import (
    RealisticMatchingEngine,
    MatchingEngineConfig,
    SimulatedOrder,
    SimOrderSide,
    SimOrderType,
    SimOrderStatus,
    FeeSchedule,
)
from mdrap_strategies.risk_engine import (
    InstitutionalRiskEngine,
    StrictRiskLimits,
)
from mdrap_strategies.microstructure import (
    compute_avellaneda_stoikov,
    compute_micro_price,
    classify_trade_lee_ready,
)
from mdrap_strategies.tear_sheet import (
    ExecutionTearSheet,
    generate_tear_sheet,
)


def run_strategy_simulation(
    strategy_type: str,
    events: list[CanonicalEvent],
    initial_capital: float = 100_000.0,
    symbol: str = "AAPL",
) -> ExecutionTearSheet:
    """
    Simulates a strategy through the RealisticMatchingEngine and InstitutionalRiskEngine.
    """
    fee_sched = FeeSchedule(maker_fee_bps=2.0, taker_fee_bps=5.0)
    config = MatchingEngineConfig(
        network_latency_ms=1.0,
        matching_delay_ms=0.5,
        fee_schedule=fee_sched,
    )
    matching = RealisticMatchingEngine(config=config)
    risk = InstitutionalRiskEngine(
        limits=StrictRiskLimits(
            max_order_qty=500.0,
            max_position_qty=2500.0,
            price_collar_bps=150.0,  # 150 bps allows reasonable MM quoting while guarding outliers
            max_drawdown_pct=5.0,
        ),
        initial_capital=initial_capital,
    )

    cash = initial_capital
    order_id_counter = 0
    orders_submitted = 0
    open_orders: dict[str, SimulatedOrder] = {}
    current_mid = 100.0
    prev_trade_px: float | None = None
    rng = random.Random(42)
    equity_curve: list[tuple[float, float]] = []
    processed_ledger_idx = 0

    # For Buy-and-Hold: buy on first event
    bought_and_held = False

    t_start = events[0].exchange_timestamp if events else 1.0
    t_end = events[-1].exchange_timestamp if events else 1000.0
    total_time = max(1.0, t_end - t_start)

    for i, evt in enumerate(events):
        t_now = evt.exchange_timestamp or (t_start + i * 0.1)

        # 1. Update matching engine market view
        if evt.event_type == EventType.QUOTE:
            b_px = evt.bid_price or (current_mid - 0.05)
            a_px = evt.ask_price or (current_mid + 0.05)
            b_sz = evt.bid_size or 50.0
            a_sz = evt.ask_size or 50.0
            current_mid = (b_px + a_px) / 2.0
            matching.update_book(symbol, bids=[(b_px, b_sz)], asks=[(a_px, a_sz)], timestamp=t_now)
            matching.process_time_slice(t_now)

        elif evt.event_type == EventType.TRADE:
            tr_px = evt.price or current_mid
            tr_sz = evt.quantity or 10.0
            current_mid = tr_px
            matching.process_market_trade(symbol, trade_price=tr_px, trade_size=tr_sz, timestamp=t_now)

        # 2. Check and record fills (process only NEW entries in trade ledger)
        ledger = matching.get_ledger()
        while processed_ledger_idx < len(ledger):
            fill = ledger[processed_ledger_idx]
            processed_ledger_idx += 1
            oid = fill["order_id"]
            if oid in open_orders:
                sz = fill["quantity"]
                px = fill["price"]
                side = fill["side"]
                fee = fill["fee"]
                cash -= fee
                if side == "BUY":
                    cash -= (sz * px)
                else:
                    cash += (sz * px)
                risk.update_position(symbol, fill_qty=sz, fill_price=px, side=side)
                # Update portfolio equity
                unrealized = sum(
                    q * matching._mid_prices.get(s, px)
                    for s, q in risk.positions.items()
                )
                eq = cash + unrealized
                risk.update_portfolio_equity(eq)
                equity_curve.append((t_now, eq))

        # Record equity periodically
        if i % 100 == 0:
            unrealized = sum(
                q * matching._mid_prices.get(s, current_mid)
                for s, q in risk.positions.items()
            )
            equity_curve.append((t_now, cash + unrealized))

        # 3. Strategy decision logic
        if strategy_type == "NO_TRADE":
            pass

        elif strategy_type == "BUY_AND_HOLD":
            if not bought_and_held and evt.event_type == EventType.QUOTE:
                order_id_counter += 1
                oid = f"BnH-{order_id_counter}"
                v_res = risk.validate_order(
                    symbol=symbol, side="BUY", order_type="MARKET", quantity=200.0,
                    price=current_mid, prevailing_mid=current_mid
                )
                if v_res.passed:
                    ord_obj = matching.submit_order(
                        order_id=oid, symbol=symbol, side=SimOrderSide.BUY,
                        order_type=SimOrderType.MARKET, quantity=200.0, current_time=t_now
                    )
                    open_orders[oid] = ord_obj
                    orders_submitted += 1
                    bought_and_held = True

        elif strategy_type == "AS_MM":
            if evt.event_type == EventType.QUOTE:
                # Cancel old quotes
                for old_oid in list(open_orders.keys()):
                    if open_orders[old_oid].status in (SimOrderStatus.ACCEPTED, SimOrderStatus.PENDING):
                        matching.cancel_order(old_oid, current_time=t_now)
                        open_orders.pop(old_oid, None)

                # Compute Avellaneda-Stoikov quotes
                cur_pos = risk.positions.get(symbol, 0.0)
                t_rem = max(0.01, (t_end - t_now) / total_time)
                as_quotes = compute_avellaneda_stoikov(
                    mid_price=current_mid,
                    inventory=cur_pos,
                    volatility_sigma=0.015,
                    gamma=0.1,
                    kappa=1.5,
                    time_remaining=t_rem,
                )

                spread_mult = 1.5 if evt.quality_status == QualityStatus.SUSPICIOUS else 1.0
                bid_px = round(as_quotes.reservation_price - (as_quotes.optimal_half_spread * spread_mult), 2)
                ask_px = round(as_quotes.reservation_price + (as_quotes.optimal_half_spread * spread_mult), 2)

                # Check collar distance
                if abs(bid_px - current_mid) / current_mid * 10_000.0 <= 150.0:
                    v_bid = risk.validate_order(
                        symbol=symbol, side="BUY", order_type="LIMIT", quantity=25.0,
                        price=bid_px, prevailing_mid=current_mid
                    )
                    if v_bid.passed:
                        order_id_counter += 1
                        b_id = f"AS-BID-{order_id_counter}"
                        o_b = matching.submit_order(
                            order_id=b_id, symbol=symbol, side=SimOrderSide.BUY,
                            order_type=SimOrderType.LIMIT, quantity=25.0, price=bid_px, current_time=t_now
                        )
                        open_orders[b_id] = o_b
                        orders_submitted += 1

                if abs(ask_px - current_mid) / current_mid * 10_000.0 <= 150.0:
                    v_ask = risk.validate_order(
                        symbol=symbol, side="SELL", order_type="LIMIT", quantity=25.0,
                        price=ask_px, prevailing_mid=current_mid
                    )
                    if v_ask.passed:
                        order_id_counter += 1
                        a_id = f"AS-ASK-{order_id_counter}"
                        o_a = matching.submit_order(
                            order_id=a_id, symbol=symbol, side=SimOrderSide.SELL,
                            order_type=SimOrderType.LIMIT, quantity=25.0, price=ask_px, current_time=t_now
                        )
                        open_orders[a_id] = o_a
                        orders_submitted += 1

        elif strategy_type == "SIMPLE_MM":
            if evt.event_type == EventType.QUOTE:
                for old_oid in list(open_orders.keys()):
                    if open_orders[old_oid].status in (SimOrderStatus.ACCEPTED, SimOrderStatus.PENDING):
                        matching.cancel_order(old_oid, current_time=t_now)
                        open_orders.pop(old_oid, None)

                # Fixed symmetric spread (10 bps), no inventory skew
                half_spread = current_mid * (10.0 / 10_000.0)
                bid_px = round(current_mid - half_spread, 2)
                ask_px = round(current_mid + half_spread, 2)

                v_bid = risk.validate_order(symbol=symbol, side="BUY", order_type="LIMIT", quantity=25.0, price=bid_px, prevailing_mid=current_mid)
                if v_bid.passed:
                    order_id_counter += 1
                    b_id = f"SMM-BID-{order_id_counter}"
                    o_b = matching.submit_order(order_id=b_id, symbol=symbol, side=SimOrderSide.BUY, order_type=SimOrderType.LIMIT, quantity=25.0, price=bid_px, current_time=t_now)
                    open_orders[b_id] = o_b
                    orders_submitted += 1

                v_ask = risk.validate_order(symbol=symbol, side="SELL", order_type="LIMIT", quantity=25.0, price=ask_px, prevailing_mid=current_mid)
                if v_ask.passed:
                    order_id_counter += 1
                    a_id = f"SMM-ASK-{order_id_counter}"
                    o_a = matching.submit_order(order_id=a_id, symbol=symbol, side=SimOrderSide.SELL, order_type=SimOrderType.LIMIT, quantity=25.0, price=ask_px, current_time=t_now)
                    open_orders[a_id] = o_a
                    orders_submitted += 1

        elif strategy_type == "WHALE_MOMENTUM":
            if evt.event_type == EventType.TRADE:
                px = evt.price or current_mid
                sz = evt.quantity or 10.0
                notional = px * sz
                if notional >= 50_000.0 or sz >= 400.0:
                    side_aggressor = classify_trade_lee_ready(
                        trade_price=px, prev_trade_price=prev_trade_px,
                        bid_price=current_mid - 0.05, ask_price=current_mid + 0.05
                    )
                    sim_side = SimOrderSide.BUY if side_aggressor == "BUY" else SimOrderSide.SELL
                    v_res = risk.validate_order(
                        symbol=symbol, side=sim_side.value, order_type="MARKET",
                        quantity=50.0, price=px, prevailing_mid=current_mid
                    )
                    if v_res.passed:
                        order_id_counter += 1
                        w_id = f"WHALE-{order_id_counter}"
                        ord_obj = matching.submit_order(
                            order_id=w_id, symbol=symbol, side=sim_side,
                            order_type=SimOrderType.MARKET, quantity=50.0, current_time=t_now
                        )
                        open_orders[w_id] = ord_obj
                        orders_submitted += 1
                prev_trade_px = px

        elif strategy_type == "RANDOM_SIGNAL":
            if rng.random() < 0.05:
                side_choice = SimOrderSide.BUY if rng.random() < 0.5 else SimOrderSide.SELL
                v_res = risk.validate_order(
                    symbol=symbol, side=side_choice.value, order_type="MARKET",
                    quantity=20.0, price=current_mid, prevailing_mid=current_mid
                )
                if v_res.passed:
                    order_id_counter += 1
                    r_id = f"RND-{order_id_counter}"
                    ord_obj = matching.submit_order(
                        order_id=r_id, symbol=symbol, side=side_choice,
                        order_type=SimOrderType.MARKET, quantity=20.0, current_time=t_now
                    )
                    open_orders[r_id] = ord_obj
                    orders_submitted += 1

    # End of run: flush
    matching.process_time_slice(t_end + 1.0)
    cur_inv = risk.positions.get(symbol, 0.0)

    tear_sheet = generate_tear_sheet(
        strategy_name=strategy_type,
        symbol=symbol,
        initial_capital=initial_capital,
        current_cash=cash,
        current_inventory=cur_inv,
        current_mid_price=current_mid,
        orders=list(matching.orders.values()),
        trade_ledger=matching.get_ledger(),
        equity_curve=equity_curve,
        adverse_markouts=matching.compute_adverse_selection_markout(horizon_ms=100.0),
    )
    return tear_sheet


def run_scaling_benchmark_worker(events_per_worker: int) -> float:
    """Worker task for multi-worker scaling benchmark."""
    cfg = SimulatorConfig(seed=42, num_events=events_per_worker, instruments=["AAPL", "MSFT"])
    sim = FeedSimulator(cfg)
    fqe = FastQualityEngine()
    count = 0
    t0 = time.perf_counter()
    for raw, _ in sim.generate():
        try:
            canon = normalize(raw)
            _ = fqe.evaluate(canon)
            count += 1
        except Exception:
            pass
    elapsed = time.perf_counter() - t0
    return count / elapsed if elapsed > 0 else 0.0


def run_multi_worker_benchmark(worker_counts: list[int] = [1, 2, 4, 8]) -> dict[int, dict[str, float]]:
    """Benchmarks pipeline throughput scaling across 1, 2, 4, and 8 worker processes."""
    results = {}
    events_per_worker = 10_000

    for n_workers in worker_counts:
        t0 = time.perf_counter()
        if n_workers == 1:
            rate = run_scaling_benchmark_worker(events_per_worker)
            total_events = events_per_worker
            elapsed = events_per_worker / rate if rate > 0 else 0.0
            throughput = rate
        else:
            with multiprocessing.Pool(processes=n_workers) as pool:
                rates = pool.map(run_scaling_benchmark_worker, [events_per_worker] * n_workers)
            elapsed = time.perf_counter() - t0
            total_events = events_per_worker * n_workers
            throughput = total_events / elapsed if elapsed > 0 else 0.0

        latency_us = (elapsed / total_events) * 1_000_000.0 if total_events > 0 else 0.0
        results[n_workers] = {
            "workers": n_workers,
            "total_events": total_events,
            "elapsed_seconds": round(elapsed, 3),
            "throughput_eps": round(throughput, 1),
            "latency_us": round(latency_us, 2),
            "speedup": round(throughput / results[1]["throughput_eps"], 2) if 1 in results else 1.0,
        }
    return results


def main():
    print("=" * 80)
    print("MDRAP v5.1.1 QUANTITATIVE STRATEGY EVALUATION & MULTI-BASELINE SUITE")
    print("=" * 80)

    # 1. Generate realistic dataset (5,000 market events)
    print("\n[1/3] Generating deterministic market event dataset (N = 5,000)...")
    sim = FeedSimulator(SimulatorConfig(seed=42, num_events=5000, instruments=["AAPL"], sources=["FEEDX", "FEEDY"]))
    events: list[CanonicalEvent] = []
    fqe = FastQualityEngine()
    for raw, _ in sim.generate():
        try:
            canon = normalize(raw)
            eval_res = fqe.evaluate(canon)
            events.append(eval_res)
        except SchemaError:
            continue
    print(f"Dataset generated: {len(events):,} validated canonical events.")

    # 2. Split into In-Sample (60%) and Out-of-Sample (40%)
    split_idx = int(len(events) * 0.60)
    is_events = events[:split_idx]
    oos_events = events[split_idx:]
    print(f"Data split: In-Sample = {len(is_events):,} events (60%), Out-of-Sample = {len(oos_events):,} events (40%)")

    strategies = [
        "AS_MM",
        "WHALE_MOMENTUM",
        "BUY_AND_HOLD",
        "NO_TRADE",
        "SIMPLE_MM",
        "RANDOM_SIGNAL",
    ]

    all_results = {"in_sample": {}, "out_of_sample": {}, "full_sample": {}}

    print("\n[2/3] Simulating all 6 strategies under realistic matching & institutional risk gates...")
    for strat in strategies:
        print(f"  -> Running {strat}...")
        ts_full = run_strategy_simulation(strat, events)
        ts_is = run_strategy_simulation(strat, is_events)
        ts_oos = run_strategy_simulation(strat, oos_events)

        all_results["full_sample"][strat] = ts_full.to_dict()
        all_results["in_sample"][strat] = ts_is.to_dict()
        all_results["out_of_sample"][strat] = ts_oos.to_dict()

    # Print summary table
    print("\n" + "=" * 105)
    print(f"{'Strategy / Model':<22} | {'Gross PnL':>10} | {'Fees Paid':>10} | {'Net PnL':>10} | {'Sharpe':>7} | {'MaxDD %':>8} | {'Win Rate':>8} | {'Trades':>6}")
    print("-" * 105)
    for strat in strategies:
        res = all_results["full_sample"][strat]
        print(
            f"{strat:<22} | ${res['gross_pnl']:>9,.2f} | ${res['fees_paid']:>9,.2f} | ${res['net_pnl']:>9,.2f} | "
            f"{res['sharpe_ratio']:>7.2f} | {res['max_drawdown_pct']:>7.2f}% | {res['win_rate_pct']:>7.1f}% | {res['total_trades']:>6}"
        )
    print("=" * 105)

    # 3. Run multi-worker scaling benchmark
    print("\n[3/3] Running multi-worker concurrency benchmark (1, 2, 4, 8 workers)...")
    scaling_res = run_multi_worker_benchmark([1, 2, 4, 8])
    print("\n" + "=" * 80)
    print(f"{'Workers':<10} | {'Total Events':>14} | {'Elapsed (s)':>12} | {'Throughput (EPS)':>18} | {'Speedup':>10}")
    print("-" * 80)
    for w, r in scaling_res.items():
        print(f"{w:<10} | {r['total_events']:>14,} | {r['elapsed_seconds']:>12.3f} | {r['throughput_eps']:>18,.1f} | {r['speedup']:>9.2f}x")
    print("=" * 80)

    # Save to JSON
    output_dir = ROOT / "audit" / "v5.1.1"
    output_dir.mkdir(parents=True, exist_ok=True)
    out_file = output_dir / "quantitative_strategy_evaluation.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": time.time(),
            "strategies": all_results,
            "scaling": scaling_res,
        }, f, indent=2)
    print(f"\nSaved evaluation results to {out_file}")


if __name__ == "__main__":
    main()
