import bisect, time, json, math, os, sys
from collections import defaultdict

sys.path.insert(0, "src")
sys.path.insert(0, "tools")
sys.path.insert(0, "packages/mdrap-strategies")

from replay_l2 import stream_captured_frames, L2BookReconstructor
from run_shadow_trading import FixedSpreadMM, InventorySkewMM, AvellanedaStoikovMM
from mdrap_strategies.fx_basis import DenominationAwareValuator
from mdrap_strategies.matching_engine import SimOrderSide


def fast_compute_markouts(
    ledger, price_history, horizons_ms=[100.0, 1000.0, 5000.0, 10000.0]
):
    """Compute markouts using binary search (O(log N)) instead of linear scan."""
    hist_ts = {}
    hist_mids = {}
    for sym, hist in price_history.items():
        if hist:
            hist_ts[sym] = [h[0] for h in hist]
            hist_mids[sym] = [h[1] for h in hist]

    results_by_horizon = {h: [] for h in horizons_ms}

    for trade in ledger:
        sym = trade["symbol"]
        t_fill = trade["timestamp"]
        mid_t = trade["mid_at_fill"]
        if sym not in hist_ts or not hist_ts[sym]:
            continue

        ts_list = hist_ts[sym]
        mids_list = hist_mids[sym]
        direction = 1.0 if trade["side"] == "BUY" else -1.0

        for h in horizons_ms:
            target_t = t_fill + (h / 1000.0)
            idx = bisect.bisect_left(ts_list, target_t)
            if idx < len(ts_list):
                mid_future = mids_list[idx]
            else:
                mid_future = mids_list[-1]

            markout = direction * (mid_future - mid_t)
            markout_bps = (markout / mid_t) * 10000.0 if mid_t > 0 else 0.0
            results_by_horizon[h].append(
                {
                    "symbol": sym,
                    "side": trade["side"],
                    "venue": sym.split(":")[0] if ":" in sym else "UNKNOWN",
                    "instrument": sym.split(":")[-1] if ":" in sym else sym,
                    "markout_bps": markout_bps,
                    "mid_fill": mid_t,
                    "mid_future": mid_future,
                }
            )

    return results_by_horizon


def run_fast_forensics(max_frames=120000):
    t0 = time.time()
    cap_path = "audit/v6.0.4/l2_capture_2hour.jsonl.gz"
    valuator = DenominationAwareValuator()
    reconstructor = L2BookReconstructor()

    strats = {
        "Fixed-Spread Post-Only": FixedSpreadMM(
            initial_capital=100_000.0,
            post_only=True,
            name="Fixed-Spread Post-Only [ABANDONED]",
        ),
        "Inventory-Skewed": InventorySkewMM(
            initial_capital=100_000.0,
            penalty_rate=0.04,
            name="Inventory-Skewed (pen=0.04)",
        ),
        "Avellaneda-Stoikov": AvellanedaStoikovMM(
            initial_capital=100_000.0, gamma=0.50, name="Avellaneda-Stoikov (γ=0.50)"
        ),
    }

    current_mids = {}
    quote_timestamps = defaultdict(dict)
    quote_ages_by_strat = defaultdict(list)
    count = 0

    print(f"Streaming {max_frames} frames...", flush=True)

    for frame in stream_captured_frames(cap_path):
        count += 1
        if count > max_frames:
            break

        events = reconstructor.process_frame(frame)
        if not events:
            continue

        for ev in events:
            if ev.event_kind == "CONTROL":
                continue
            sym_key = f"{ev.venue}:{ev.instrument}"
            is_usdt = ev.venue in ("BINANCE", "OKX", "BYBIT")
            rate_usdt = valuator.get_fx_rate("USDT")
            fx_mult = rate_usdt if is_usdt else 1.0

            if ev.event_kind in ("BOOK_SNAPSHOT", "BOOK_DELTA") and ev.bids and ev.asks:
                bb_px, bb_sz = ev.bids[0]
                ba_px, ba_sz = ev.asks[0]
                bb_usd = bb_px * fx_mult
                ba_usd = ba_px * fx_mult
                mid_usd = (bb_usd + ba_usd) / 2.0
                current_mids[ev.instrument] = mid_usd
                current_mids[sym_key] = mid_usd

                for s_name, strat in strats.items():
                    strat.engine.update_book(
                        sym_key, [(bb_usd, bb_sz)], [(ba_usd, ba_sz)], ev.exchange_ts
                    )
                    strat.on_validated_quote(
                        ev.venue,
                        ev.instrument,
                        bb_usd,
                        ba_usd,
                        bb_sz,
                        ba_sz,
                        ev.exchange_ts,
                    )
                    strat.engine.process_time_slice(ev.exchange_ts)
                    quote_timestamps[s_name][sym_key] = ev.exchange_ts

            elif (
                ev.event_kind == "TRADE"
                and ev.price is not None
                and ev.quantity is not None
                and not ev.duplicate
            ):
                tr_px_usd = ev.price * fx_mult
                for s_name, strat in strats.items():
                    prev_len = len(strat.engine.get_ledger())
                    strat.engine.process_market_trade(
                        symbol=sym_key,
                        trade_price=tr_px_usd,
                        trade_size=ev.quantity,
                        timestamp=ev.exchange_ts,
                        aggressor_side=ev.aggressor_side,
                    )
                    strat.sync_fills(ev.exchange_ts, current_mids)
                    new_trades = strat.engine.get_ledger()[prev_len:]
                    for trade in new_trades:
                        last_q_ts = quote_timestamps[s_name].get(
                            trade["symbol"], ev.exchange_ts
                        )
                        q_age = max(0.0, (ev.exchange_ts - last_q_ts) * 1000.0)
                        quote_ages_by_strat[s_name].append(q_age)

    print(
        f"Replay completed in {time.time() - t0:.2f}s! Computing forensic metrics...",
        flush=True,
    )

    results = {}
    for s_name, strat in strats.items():
        strat.sync_order_metrics()
        ledger = strat.engine.get_ledger()
        if not ledger:
            continue

        mk_res = fast_compute_markouts(ledger, strat.engine._price_history)

        # Spread capture using EXACT trade['mid_at_fill']
        sc_buys = []
        sc_sells = []
        sc_btc = []
        sc_eth = []
        sc_all = []

        for trade in ledger:
            mid = trade["mid_at_fill"]
            px = trade["price"]
            sym = trade["symbol"]
            if trade["side"] == "BUY":
                sc = (mid - px) / mid * 10000.0
                sc_buys.append(sc)
            else:
                sc = (px - mid) / mid * 10000.0
                sc_sells.append(sc)
            sc_all.append(sc)
            if "BTC" in sym:
                sc_btc.append(sc)
            elif "ETH" in sym:
                sc_eth.append(sc)

        mean_sc = sum(sc_all) / len(sc_all) if sc_all else 0.0

        # Horizons summary
        h_means = {}
        for h, records in mk_res.items():
            h_means[f"{int(h)}ms"] = (
                sum(r["markout_bps"] for r in records) / len(records)
                if records
                else 0.0
            )

        # Markout 1s breakdowns
        m1s = mk_res.get(1000.0, [])
        m1s_buys = [r["markout_bps"] for r in m1s if r["side"] == "BUY"]
        m1s_sells = [r["markout_bps"] for r in m1s if r["side"] == "SELL"]
        m1s_btc = [r["markout_bps"] for r in m1s if "BTC" in r["instrument"]]
        m1s_eth = [r["markout_bps"] for r in m1s if "ETH" in r["instrument"]]

        m1s_venues = defaultdict(list)
        for r in m1s:
            m1s_venues[r["venue"]].append(r["markout_bps"])
        venue_markouts = {v: sum(vals) / len(vals) for v, vals in m1s_venues.items()}

        # Quote age
        q_ages = sorted(quote_ages_by_strat[s_name])
        q_p50 = q_ages[len(q_ages) // 2] if q_ages else 0.0
        q_p95 = q_ages[int(len(q_ages) * 0.95)] if q_ages else 0.0

        # Net edge (Spread Capture + 1s Markout)
        net_edge = mean_sc + h_means["1000ms"]

        results[s_name] = {
            "fills_analyzed": len(ledger),
            "buys": len([t for t in ledger if t["side"] == "BUY"]),
            "sells": len([t for t in ledger if t["side"] == "SELL"]),
            "btc_fills": len([t for t in ledger if "BTC" in t["symbol"]]),
            "eth_fills": len([t for t in ledger if "ETH" in t["symbol"]]),
            "spread_capture": {
                "overall_bps": round(mean_sc, 4),
                "buy_bps": round(sum(sc_buys) / len(sc_buys) if sc_buys else 0.0, 4),
                "sell_bps": round(
                    sum(sc_sells) / len(sc_sells) if sc_sells else 0.0, 4
                ),
                "btc_bps": round(sum(sc_btc) / len(sc_btc) if sc_btc else 0.0, 4),
                "eth_bps": round(sum(sc_eth) / len(sc_eth) if sc_eth else 0.0, 4),
            },
            "markouts": {k: round(v, 4) for k, v in h_means.items()},
            "markout_1s_breakdown": {
                "buy_bps": round(sum(m1s_buys) / len(m1s_buys) if m1s_buys else 0.0, 4),
                "sell_bps": round(
                    sum(m1s_sells) / len(m1s_sells) if m1s_sells else 0.0, 4
                ),
                "btc_bps": round(sum(m1s_btc) / len(m1s_btc) if m1s_btc else 0.0, 4),
                "eth_bps": round(sum(m1s_eth) / len(m1s_eth) if m1s_eth else 0.0, 4),
                "by_venue": {v: round(val, 4) for v, val in venue_markouts.items()},
            },
            "net_edge_per_fill_bps": round(net_edge, 4),
            "quote_age_ms": {
                "median": round(q_p50, 2),
                "p95": round(q_p95, 2),
            },
        }

    out_file = "audit/v6.0.4.1/forensics_detailed.json"
    with open(out_file, "w") as f:
        json.dump(results, f, indent=2)

    print("Forensics saved to:", out_file, flush=True)
    for s_name, res in results.items():
        print(f"\n==========================================")
        print(f"STRATEGY: {s_name}")
        print(f"==========================================")
        print(
            f"  Fills Analyzed:       {res['fills_analyzed']} (Buys: {res['buys']}, Sells: {res['sells']})"
        )
        print(
            f"  Instruments:          BTC: {res['btc_fills']}, ETH: {res['eth_fills']}"
        )
        print(
            f"  Spread Capture:       {res['spread_capture']['overall_bps']:+.4f} bps (Buy: {res['spread_capture']['buy_bps']:+.4f}, Sell: {res['spread_capture']['sell_bps']:+.4f})"
        )
        print(
            f"  Asset Spread Capture: BTC: {res['spread_capture']['btc_bps']:+.4f} bps, ETH: {res['spread_capture']['eth_bps']:+.4f} bps"
        )
        print(
            f"  Markouts Across Time: 100ms={res['markouts']['100ms']:+.4f} bps, 1s={res['markouts']['1000ms']:+.4f} bps, 5s={res['markouts']['5000ms']:+.4f} bps, 10s={res['markouts']['10000ms']:+.4f} bps"
        )
        print(
            f"  1s Markout by Side:   Buy: {res['markout_1s_breakdown']['buy_bps']:+.4f} bps, Sell: {res['markout_1s_breakdown']['sell_bps']:+.4f} bps"
        )
        print(
            f"  1s Markout by Asset:  BTC: {res['markout_1s_breakdown']['btc_bps']:+.4f} bps, ETH: {res['markout_1s_breakdown']['eth_bps']:+.4f} bps"
        )
        print(f"  1s Markout by Venue:  {res['markout_1s_breakdown']['by_venue']}")
        print(
            f"  Net Edge per Trade:   {res['net_edge_per_fill_bps']:+.4f} bps (Spread Capture + 1s Markout)"
        )
        print(
            f"  Quote Age at Fill:    Median={res['quote_age_ms']['median']:.1f} ms, p95={res['quote_age_ms']['p95']:.1f} ms"
        )


if __name__ == "__main__":
    run_fast_forensics()
