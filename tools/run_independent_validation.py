"""
MDRAP 6.0.3.3 — Independent Strategy Holdout Validation Harness.

Executes rigorous independent holdout testing across 5 distinct market regimes:
1. Bullish Trend (positive drift, moderate vol)
2. Bearish Trend (negative drift, elevated vol)
3. Sideways / Range-Bound (zero drift, mean-reverting)
4. High Volatility Shock (wide spreads, volatile spikes)
5. Low Volatility Compression (compressed spreads, tight ranges)

Across all 5 exchanges (BINANCE, COINBASE, KRAKEN, OKX, BYBIT) and BTC/USD & ETH/USD.
Compares:
- Fixed-Spread Post-Only
- Fixed-Spread Baseline (unconstrained control)
- Inventory-Skewed (penalty=0.04)
- Avellaneda-Stoikov (gamma=0.50)
- No-Trade Baseline
"""

import sys
import os
import time
import math
import random
import collections
from datetime import datetime, timezone
from pathlib import Path

# Setup paths
root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root / "tools"))
sys.path.insert(0, str(root / "src"))
sys.path.insert(0, str(root / "packages" / "mdrap-strategies"))

from mdrap.models import RawEvent, QualityStatus
from mdrap.config import DEFAULT_ASSET_CLASSES
from mdrap.gateway import Gateway
from mdrap.quality import QualityEngine, QualityConfig
from mdrap.fastpath import FastQualityEngine, is_available as is_fastpath_available
from mdrap.ws_feed import SequenceScope
from mdrap_strategies.matching_engine import (
    RealisticMatchingEngine,
    MatchingEngineConfig,
    FeeSchedule,
    SimOrderSide,
    SimOrderType,
    SimOrderStatus,
)
from mdrap_strategies.fx_basis import DenominationAwareValuator
from run_shadow_trading import (
    BaseShadowStrategy,
    AvellanedaStoikovMM,
    InventorySkewMM,
    FixedSpreadMM,
    NoTradeBaseline,
)


def run_regime_simulation(
    regime_name: str,
    seed: int,
    drift_per_sec: float,
    vol_btc: float,
    vol_eth: float,
    mean_reversion: float = 0.0,
    total_seconds: float = 14_400.0,  # 4.0 hours = 7,200 continuous steps
):
    rng = random.Random(seed)
    now_ts = 1_700_000_000.0  # Deterministic epoch
    sim_step_s = 2.0
    total_steps = int(total_seconds / sim_step_s)

    venues = ["BINANCE", "COINBASE", "KRAKEN", "OKX", "BYBIT"]
    symbols = ["BTC/USD", "ETH/USD"]

    # Initial prices
    mids = {"BTC/USD": 91_250.0, "ETH/USD": 3_120.0}
    anchor_mids = dict(mids)
    vol_per_sec = {"BTC/USD": vol_btc, "ETH/USD": vol_eth}

    venue_spreads = {
        "BINANCE": {"BTC/USD": 0.50, "ETH/USD": 0.05},
        "COINBASE": {"BTC/USD": 0.50, "ETH/USD": 0.05},
        "KRAKEN": {"BTC/USD": 1.00, "ETH/USD": 0.10},
        "OKX": {"BTC/USD": 0.75, "ETH/USD": 0.08},
        "BYBIT": {"BTC/USD": 0.80, "ETH/USD": 0.08},
    }

    # If high vol regime, widen spreads slightly to match market reality
    if "High Vol" in regime_name:
        for v in venue_spreads:
            venue_spreads[v]["BTC/USD"] *= 1.5
            venue_spreads[v]["ETH/USD"] *= 1.5

    # Target strategies (Fixed-Spread Post-Only retained as ABANDONED / RESEARCH ONLY)
    strats: list[BaseShadowStrategy] = [
        FixedSpreadMM(initial_capital=100_000.0, post_only=True, name="Fixed-Spread Post-Only [ABANDONED]"),
        FixedSpreadMM(initial_capital=100_000.0, post_only=False, name="Fixed-Spread Baseline [ABANDONED]"),
        InventorySkewMM(initial_capital=100_000.0, penalty_rate=0.04, name="Inventory-Skewed (pen=0.04)"),
        AvellanedaStoikovMM(initial_capital=100_000.0, gamma=0.50, name="Avellaneda-Stoikov (γ=0.50)"),
        NoTradeBaseline(initial_capital=100_000.0),
    ]

    gateway = Gateway()
    # Use DEFAULT_ASSET_CLASSES so independently sequenced crypto channels split sequence domains
    # consistently across both Pure Python QualityEngine and FastQualityEngine.
    q_cfg = QualityConfig(
        asset_classes=dict(DEFAULT_ASSET_CLASSES),
        unseq_dup_status="SUSPICIOUS",
    )
    if is_fastpath_available() and os.environ.get("MDRAP_DISABLE_FASTPATH") != "1":
        quality_engine = FastQualityEngine(q_cfg)
    else:
        quality_engine = QualityEngine(q_cfg)

    valuator = DenominationAwareValuator()
    current_mids = dict(mids)

    cur_t = now_ts
    total_quotes = 0
    total_trades = 0
    session_epoch = 1
    # Independent per-(venue, instrument, channel, epoch) sequence counters — never global step+1
    stream_seq: dict[str, int] = collections.defaultdict(int)

    t_start = time.time()

    for step in range(total_steps):
        cur_t += sim_step_s

        # 1. Price evolution
        for sym in symbols:
            drift = drift_per_sec * sim_step_s
            shock = rng.gauss(0.0, vol_per_sec[sym] * math.sqrt(sim_step_s))
            mr = -mean_reversion * (mids[sym] - anchor_mids[sym])
            mids[sym] = round(mids[sym] * (1.0 + drift + shock + mr), 2)
            current_mids[sym] = mids[sym]

        # 2. Quotes across venues
        active_venues = venues if (step % 2 == 0) else rng.sample(venues, 3)
        for venue in active_venues:
            for sym in symbols:
                total_quotes += 1
                mid = mids[sym]
                rate_usdt = valuator.get_fx_rate("USDT")
                is_usdt_venue = venue in ("BINANCE", "OKX", "BYBIT")
                fx_mult = (1.0 / rate_usdt) if is_usdt_venue else 1.0

                half_spr = venue_spreads[venue][sym] / 2.0
                bid_px = round((mid - half_spr) * fx_mult, 2)
                ask_px = round((mid + half_spr) * fx_mult, 2)
                is_btc = "BTC" in sym
                bid_sz = round(rng.uniform(0.5, 3.5) if is_btc else rng.uniform(5.0, 35.0), 3)
                ask_sz = round(rng.uniform(0.5, 3.5) if is_btc else rng.uniform(5.0, 35.0), 3)

                q_scope = SequenceScope(
                    venue=venue,
                    instrument=sym,
                    channel="QUOTE",
                    epoch=session_epoch,
                )
                stream_seq[q_scope.key()] += 1
                q_seq = stream_seq[q_scope.key()]

                raw_q = RawEvent(
                    source=venue,
                    payload={
                        "instrument": sym,
                        "event_type": "QUOTE",
                        "channel": "QUOTE",
                        "epoch": session_epoch,
                        "bid": bid_px,
                        "ask": ask_px,
                        "bid_size": bid_sz,
                        "ask_size": ask_sz,
                        "sequence": q_seq,
                        "exchange_ts": cur_t,
                    },
                    receive_timestamp=cur_t + 0.001,
                )
                can_q = gateway.normalize(raw_q)
                eval_q = quality_engine.evaluate(can_q)
                if eval_q and eval_q.quality_status == QualityStatus.VALID:
                    q_key = f"{venue}:{sym}"
                    bid_usd = bid_px * rate_usdt if is_usdt_venue else bid_px
                    ask_usd = ask_px * rate_usdt if is_usdt_venue else ask_px
                    current_mids[q_key] = (bid_usd + ask_usd) / 2.0

                    for strat in strats:
                        strat.engine.update_book(
                            symbol=q_key,
                            bids=[(bid_usd, bid_sz)],
                            asks=[(ask_usd, ask_sz)],
                            timestamp=cur_t,
                        )
                        strat.on_validated_quote(
                            venue=venue,
                            symbol=sym,
                            bid=bid_usd,
                            ask=ask_usd,
                            bid_sz=bid_sz,
                            ask_sz=ask_sz,
                            timestamp=cur_t,
                        )

        # Advance exchange matching clock so arrivals/cancels settle chronologically even without trades
        for strat in strats:
            strat.engine.process_time_slice(cur_t + 0.003)

        # 3. Market prints (trades)
        for venue in active_venues:
            for sym in symbols:
                if rng.random() < 0.45:  # 45% chance of trade print per venue-symbol step
                    total_trades += 1
                    mid = mids[sym]
                    is_btc = "BTC" in sym
                    # Side of trade
                    side = "BUY" if rng.random() < 0.50 else "SELL"
                    half_spr = venue_spreads[venue][sym] / 2.0
                    tr_px = (mid + half_spr) if side == "BUY" else (mid - half_spr)
                    tr_sz = round(rng.uniform(0.05, 0.40) if is_btc else rng.uniform(0.5, 4.0), 3)

                    t_scope = SequenceScope(
                        venue=venue,
                        instrument=sym,
                        channel="TRADE",
                        epoch=session_epoch,
                    )
                    stream_seq[t_scope.key()] += 1

                    q_key = f"{venue}:{sym}"
                    for strat in strats:
                        strat.engine.process_market_trade(
                            symbol=q_key,
                            trade_price=tr_px,
                            trade_size=tr_sz,
                            timestamp=cur_t + 0.005,
                            aggressor_side=SimOrderSide.BUY if side == "BUY" else SimOrderSide.SELL,
                        )

        # Sync fills every 5 steps (fast O(1) incremental sync without O(N) order map scan)
        if step % 5 == 0:
            for strat in strats:
                strat.sync_fills(cur_t, current_mids, sync_orders=False)

    # Final sync with complete order metric scan
    for strat in strats:
        strat.sync_fills(cur_t, current_mids, sync_orders=True)

    elapsed_s = time.time() - t_start

    # Compile Regime Performance
    results = []
    for strat in strats:
        m = strat.metrics
        # Extract rejected Post-Only count
        po_rejected = sum(
            1 for o in strat.engine.orders.values()
            if o.status == SimOrderStatus.CANCELLED and o.reject_reason == "POST_ONLY_RESTRICTED"
        )
        total_sub = len(strat.engine.orders)
        tot_fills = len(m.fill_ledger)
        maker_fills = sum(1 for f in m.fill_ledger if f.get("is_maker", False))
        taker_fills = tot_fills - maker_fills

        unrealized = sum(
            qty * (current_mids.get(sym, strat.avg_costs.get(sym, 0.0)) - strat.avg_costs.get(sym, 0.0))
            for sym, qty in m.positions.items()
        )
        gross = m.realized_pnl + unrealized
        fees = m.total_fees_paid
        net = gross - fees

        # Max Drawdown
        max_dd = 0.0
        peak = strat.initial_capital
        for _, eq in m.equity_curve:
            if eq > peak:
                peak = eq
            dd = peak - eq
            if dd > max_dd:
                max_dd = dd

        # Adverse selection markouts (1s)
        try:
            m1s_list = strat.engine.compute_adverse_selection_markout(horizon_ms=1000.0)
            m1s = sum(x["markout_bps"] for x in m1s_list) / len(m1s_list) if m1s_list else 0.0
        except Exception:
            m1s = 0.0

        # Mean absolute position & max position
        btc_pos = [abs(f.get("running_pos_btc", 0.0)) for f in m.fill_ledger]
        eth_pos = [abs(f.get("running_pos_eth", 0.0)) for f in m.fill_ledger]
        max_btc = max(btc_pos) if btc_pos else abs(m.positions["BTC/USD"])
        max_eth = max(eth_pos) if eth_pos else abs(m.positions["ETH/USD"])

        # Per venue fill stats
        venue_fills = collections.defaultdict(int)
        for f in m.fill_ledger:
            v = f.get("venue") or f.get("symbol", "").split(":")[0]
            venue_fills[v] += 1

        # Interval Sharpe (15-min intervals = 450 steps = 900s)
        eq_curve = m.equity_curve
        sharpe = 0.0
        if len(eq_curve) > 10:
            interval_step = max(1, len(eq_curve) // 16)  # ~16 intervals in 4 hours (15-min each)
            sampled_eq = [eq_curve[i][1] for i in range(0, len(eq_curve), interval_step)]
            returns = [(sampled_eq[i] - sampled_eq[i-1]) / sampled_eq[i-1] for i in range(1, len(sampled_eq))]
            if returns:
                mean_ret = sum(returns) / len(returns)
                var_ret = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
                std_ret = math.sqrt(var_ret)
                if std_ret > 1e-7:
                    # Annualize 15-minute Sharpe: sqrt(252 * 24 * 4) = sqrt(24192) ≈ 155.5
                    sharpe = (mean_ret / std_ret) * math.sqrt(24192.0)

        results.append({
            "regime": regime_name,
            "strategy": strat.name,
            "submitted": total_sub,
            "po_rejected": po_rejected,
            "fills": tot_fills,
            "maker_fills": maker_fills,
            "taker_fills": taker_fills,
            "pct_maker": (maker_fills / tot_fills * 100.0) if tot_fills > 0 else 0.0,
            "fill_pct": (tot_fills / total_sub * 100.0) if total_sub > 0 else 0.0,
            "gross": gross,
            "fees": fees,
            "net": net,
            "max_dd": max_dd,
            "sharpe": sharpe,
            "m1s": m1s,
            "max_btc": max_btc,
            "max_eth": max_eth,
            "final_btc": m.positions["BTC/USD"],
            "final_eth": m.positions["ETH/USD"],
            "venue_fills": dict(venue_fills),
        })

    return results, elapsed_s, total_quotes, total_trades, mids


def run_all_validation():
    print("=" * 80)
    print("MDRAP 6.0.3.3 — INDEPENDENT STRATEGY HOLDOUT VALIDATION")
    print("Executing 5 Independent Market Regimes across 5 Exchanges & 2 Instruments")
    print("=" * 80)

    regimes = [
        {
            "name": "1. Bullish Trend (+2.9% Trend, Vol=0.03%)",
            "seed": 20261,
            "drift": +0.000002,
            "vol_btc": 0.00030,
            "vol_eth": 0.00040,
            "mr": 0.0,
        },
        {
            "name": "2. Bearish Trend (-2.9% Trend, Vol=0.035%)",
            "seed": 20262,
            "drift": -0.000002,
            "vol_btc": 0.00035,
            "vol_eth": 0.00045,
            "mr": 0.0,
        },
        {
            "name": "3. Sideways / Range-Bound (Zero Drift, Mean-Reverting)",
            "seed": 20263,
            "drift": 0.0,
            "vol_btc": 0.00020,
            "vol_eth": 0.00025,
            "mr": 0.000005,
        },
        {
            "name": "4. High Volatility Shock (+75% Vol Spike)",
            "seed": 20264,
            "drift": 0.0,
            "vol_btc": 0.00060,
            "vol_eth": 0.00080,
            "mr": 0.0,
        },
        {
            "name": "5. Low Volatility Compression (-50% Vol Range)",
            "seed": 20265,
            "drift": 0.0,
            "vol_btc": 0.00015,
            "vol_eth": 0.00020,
            "mr": 0.000002,
        },
    ]

    all_results = []
    t0_global = time.time()

    for r_cfg in regimes:
        print(f"\n>>> Running Holdout Regime: {r_cfg['name']} (Seed={r_cfg['seed']})...")
        res, el_s, n_q, n_t, final_mids = run_regime_simulation(
            regime_name=r_cfg["name"],
            seed=r_cfg["seed"],
            drift_per_sec=r_cfg["drift"],
            vol_btc=r_cfg["vol_btc"],
            vol_eth=r_cfg["vol_eth"],
            mean_reversion=r_cfg["mr"],
            total_seconds=14_400.0,
        )
        print(f"    Completed in {el_s:.1f}s | Quotes: {n_q:,} | Trades: {n_t:,} | Final BTC: ${final_mids['BTC/USD']:,.2f}")
        for row in res:
            all_results.append(row)

    print("\n" + "=" * 80)
    print("ALL HOLDOUT REGIMES COMPLETED — PERFORMANCE SUMMARY")
    print("=" * 80)

    # Print summary table per regime
    for r_cfg in regimes:
        r_name = r_cfg["name"]
        rows = [x for x in all_results if x["regime"] == r_name]
        print(f"\n### Regime: {r_name}")
        print(f"| Strategy | Submitted | PO Rej | Fills | Maker:Taker | % Maker | Fill % | Gross PnL ($) | Fees ($) | Net PnL ($) | Max DD ($) | Markout 1s |")
        print(f"| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |")
        for row in rows:
            print(
                f"| **{row['strategy']}** | {row['submitted']:,} | {row['po_rejected']:,} | {row['fills']:,} | "
                f"{row['maker_fills']}:{row['taker_fills']} | {row['pct_maker']:.1f}% | {row['fill_pct']:.1f}% | "
                f"${row['gross']:>+8.2f} | ${row['fees']:>7.2f} | **${row['net']:>+8.2f}** | ${row['max_dd']:>6.2f} | {row['m1s']:>+5.2f} bps |"
            )

    # Cross-Regime Aggregates
    print("\n" + "=" * 80)
    print("CROSS-REGIME AGGREGATE PERFORMANCE (ALL 5 HOLDOUT REGIMES COMBINED)")
    print("=" * 80)
    strats = ["Fixed-Spread Post-Only [ABANDONED]", "Fixed-Spread Baseline [ABANDONED]", "Inventory-Skewed (pen=0.04)", "Avellaneda-Stoikov (γ=0.50)", "No-Trade Baseline"]

    print(f"| Strategy | Regimes Profitable | Total Gross ($) | Total Fees ($) | Total Net PnL ($) | Avg Max DD ($) | Worst Max DD ($) | Avg % Maker |")
    print(f"| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |")
    for s_name in strats:
        s_rows = [x for x in all_results if x["strategy"] == s_name]
        n_prof = sum(1 for x in s_rows if x["net"] > 0)
        tot_gross = sum(x["gross"] for x in s_rows)
        tot_fees = sum(x["fees"] for x in s_rows)
        tot_net = sum(x["net"] for x in s_rows)
        avg_dd = sum(x["max_dd"] for x in s_rows) / len(s_rows)
        worst_dd = max(x["max_dd"] for x in s_rows)
        avg_maker = sum(x["pct_maker"] for x in s_rows) / len(s_rows)
        print(
            f"| **{s_name}** | **{n_prof}/5** | ${tot_gross:>+9.2f} | ${tot_fees:>8.2f} | **${tot_net:>+9.2f}** | ${avg_dd:>7.2f} | ${worst_dd:>7.2f} | {avg_maker:.1f}% |"
        )

    print(f"\nTotal Validation Execution Time: {time.time() - t0_global:.1f}s")
    return all_results


if __name__ == "__main__":
    run_all_validation()
