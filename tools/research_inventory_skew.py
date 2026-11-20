import bisect, collections, json, math, os, sys, time
from typing import Any

sys.path.insert(0, "src")
sys.path.insert(0, "tools")
sys.path.insert(0, "packages/mdrap-strategies")

from replay_l2 import stream_captured_frames, L2BookReconstructor
from run_shadow_trading import BaseShadowStrategy, NoTradeBaseline
from mdrap_strategies.matching_engine import SimOrderSide, SimOrderType, SimOrderStatus
from mdrap_strategies.fx_basis import DenominationAwareValuator

class ResearchInventorySkewMM(BaseShadowStrategy):
    """
    Modular Research Strategy for Inventory-Skewed Market Making.
    Ablation experiments:
    A. Baseline: Linear inventory penalty + passive maker clamp
    B. Nonlinear inventory penalty only
    C. Asymmetric spread widening & quote improvement only
    D. Order-Flow Imbalance (OFI) filtering only
    E. Short-term EWMA volatility & toxic flow pullback only
    F. Combined configuration
    """

    def __init__(
        self,
        name: str,
        penalty_rate: float = 0.04,
        quote_qty_btc: float = 0.02,
        quote_qty_eth: float = 0.20,
        max_inv_btc: float = 0.25,
        max_inv_eth: float = 2.50,
        initial_capital: float = 100_000.0,
        nonlinear_penalty: bool = False,
        asymmetric_widening: bool = False,
        ofi_filter: bool = False,
        ewma_volatility: bool = False,
        toxic_pullback: bool = False,
    ):
        super().__init__(name=name, initial_capital=initial_capital)
        self.penalty_rate = penalty_rate
        self.quote_qtys = {"BTC/USD": quote_qty_btc, "ETH/USD": quote_qty_eth}
        self.max_invs = {"BTC/USD": max_inv_btc, "ETH/USD": max_inv_eth}

        self.use_nonlinear = nonlinear_penalty
        self.use_asymmetric = asymmetric_widening
        self.use_ofi = ofi_filter
        self.use_ewma_vol = ewma_volatility
        self.use_toxic_pullback = toxic_pullback

        self._last_quote_time: dict[str, float] = {}
        self._active_quotes: dict[str, tuple[str, str]] = {}
        self._prev_bbo: dict[str, tuple[float, float, float, float]] = {}
        self._ofi_ema: dict[str, float] = collections.defaultdict(float)
        self._depth_ema: dict[str, float] = collections.defaultdict(lambda: 1.0)
        self._ewma_var: dict[str, float] = collections.defaultdict(lambda: 2.5e-7)  # (0.0005)^2
        self._prev_mid: dict[str, float] = {}
        self._trade_history: dict[str, collections.deque] = collections.defaultdict(lambda: collections.deque(maxlen=20))
        self._toxic_block_until: dict[str, tuple[float, str]] = {}

    def on_validated_trade(
        self,
        venue: str,
        symbol: str,
        price: float,
        size: float,
        side: str | None,
        timestamp: float,
    ) -> None:
        if not self.use_toxic_pullback or not side:
            return
        key = f"{venue}:{symbol}"
        hist = self._trade_history[key]
        side_norm = side.upper()
        hist.append((side_norm, timestamp))

        if len(hist) >= 4:
            recent_4 = [s for s, _ in list(hist)[-4:]]
            if all(s == "SELL" for s in recent_4):
                self._toxic_block_until[key] = (timestamp + 0.500, "BUY")
            elif all(s == "BUY" for s in recent_4):
                self._toxic_block_until[key] = (timestamp + 0.500, "SELL")

    def on_validated_quote(
        self,
        venue: str,
        symbol: str,
        bid: float,
        ask: float,
        bid_sz: float,
        ask_sz: float,
        timestamp: float,
    ) -> None:
        key = f"{venue}:{symbol}"
        last_t = self._last_quote_time.get(key, 0.0)
        if (timestamp - last_t) < 0.250:
            return
        self._last_quote_time[key] = timestamp

        mid = (bid + ask) / 2.0
        q = self.metrics.positions[symbol]
        max_inv = self.max_invs.get(symbol, 0.25)
        quote_qty = self.quote_qtys.get(symbol, 0.02)
        spread = ask - bid
        half_spread = max(0.50, spread / 2.0)
        min_tick = 0.50 if "BTC" in symbol else 0.01

        # 1. EWMA Volatility Update
        prev_m = self._prev_mid.get(key, mid)
        self._prev_mid[key] = mid
        if prev_m > 0:
            ret = (mid - prev_m) / prev_m
            old_var = self._ewma_var[key]
            new_var = 0.95 * old_var + 0.05 * (ret ** 2)
            self._ewma_var[key] = new_var
        else:
            new_var = self._ewma_var[key]

        vol_sigma = math.sqrt(max(1e-8, new_var))
        if self.use_ewma_vol:
            vol_mult = max(1.0, vol_sigma / 0.0005)
            half_spread = half_spread * vol_mult

        # 2. Causal OFI Update
        ofi_metric = 0.0
        if key in self._prev_bbo:
            pb_px, pb_sz, pa_px, pa_sz = self._prev_bbo[key]
            d_bid = bid_sz if bid > pb_px else (bid_sz - pb_sz if bid == pb_px else -pb_sz)
            d_ask = -ask_sz if ask < pa_px else (ask_sz - pa_sz if ask == pa_px else pa_sz)
            raw_ofi = d_bid - d_ask
            depth_delta = abs(d_bid) + abs(d_ask) + 1e-4

            self._ofi_ema[key] = 0.90 * self._ofi_ema[key] + 0.10 * raw_ofi
            self._depth_ema[key] = 0.90 * self._depth_ema[key] + 0.10 * depth_delta

            if self._depth_ema[key] > 0:
                ofi_metric = max(-1.0, min(1.0, self._ofi_ema[key] / self._depth_ema[key]))
        self._prev_bbo[key] = (bid, bid_sz, ask, ask_sz)

        # 3. Reservation Price
        u = max(-1.0, min(1.0, q / max_inv)) if max_inv > 0 else 0.0

        if self.use_nonlinear:
            skew_shift = math.copysign(1.0, u) * half_spread * (abs(u) + 3.0 * (abs(u) ** 3))
            reservation = mid - skew_shift
        else:
            penalty = self.penalty_rate * (mid * 0.001)
            reservation = mid - (q * penalty)

        if self.use_ofi:
            reservation -= (ofi_metric * half_spread * 0.75)

        # 4. Spreads & Quotes
        if self.use_asymmetric:
            if u > 0:
                spr_b = half_spread * (1.0 + 2.5 * abs(u))
                spr_a = max(min_tick, half_spread * (1.0 - 0.5 * abs(u)))
                raw_b = round(reservation - spr_b, 2)
                raw_a = round(reservation + spr_a, 2)
                bid_px = min(bid, raw_b)
                ask_px = max(bid + min_tick, min(ask, raw_a))
            else:
                spr_a = half_spread * (1.0 + 2.5 * abs(u))
                spr_b = max(min_tick, half_spread * (1.0 - 0.5 * abs(u)))
                raw_b = round(reservation - spr_b, 2)
                raw_a = round(reservation + spr_a, 2)
                ask_px = max(ask, raw_a)
                bid_px = min(ask - min_tick, max(bid, raw_b))
        else:
            raw_b = round(reservation - half_spread, 2)
            raw_a = round(reservation + half_spread, 2)
            bid_px = min(bid, raw_b)
            ask_px = max(ask, raw_a)

        if ask_px <= bid_px:
            ask_px = round(bid_px + min_tick, 2)

        # 5. Toxic & OFI Block Gates
        block_buy = False
        block_sell = False

        if self.use_toxic_pullback:
            toxic_info = self._toxic_block_until.get(key)
            if toxic_info:
                until_t, blocked_side = toxic_info
                if timestamp < until_t:
                    if blocked_side == "BUY":
                        block_buy = True
                    elif blocked_side == "SELL":
                        block_sell = True
                else:
                    self._toxic_block_until.pop(key, None)

        if self.use_ofi:
            if ofi_metric < -0.60:
                block_buy = True
            elif ofi_metric > 0.60:
                block_sell = True

        # 6. Submit Orders (strict Post-Only)
        if key in self._active_quotes:
            old_b, old_a = self._active_quotes[key]
            self.engine.cancel_order(old_b, current_time=timestamp)
            self.engine.cancel_order(old_a, current_time=timestamp)

        bid_id, ask_id = "", ""
        if q < max_inv and not block_buy:
            chk = self.risk_engine.validate_order(symbol, "BUY", "LIMIT", quote_qty, bid_px, mid, timestamp, venue, current_time=timestamp)
            if chk.passed:
                b_ord = self.engine.submit_order(
                    self.next_order_id(), key, SimOrderSide.BUY, SimOrderType.LIMIT,
                    quote_qty, bid_px, timestamp, f"InvSkew Bid (q={q:.3f})",
                    post_only=True,
                )
                b_ord.venue = venue
                bid_id = b_ord.order_id

        if q > -max_inv and not block_sell:
            chk = self.risk_engine.validate_order(symbol, "SELL", "LIMIT", quote_qty, ask_px, mid, timestamp, venue, current_time=timestamp)
            if chk.passed:
                a_ord = self.engine.submit_order(
                    self.next_order_id(), key, SimOrderSide.SELL, SimOrderType.LIMIT,
                    quote_qty, ask_px, timestamp, f"InvSkew Ask (q={q:.3f})",
                    post_only=True,
                )
                a_ord.venue = venue
                ask_id = a_ord.order_id

        self._active_quotes[key] = (bid_id, ask_id)

    def sync_fills(self, current_time: float, mid_prices: dict[str, float], sync_orders: bool = False) -> None:
        """Optimized O(1) incremental fill sync avoiding full ledger copying."""
        self.engine.process_time_slice(current_time)
        ledger = self.engine._trade_ledger
        if len(ledger) <= self._processed_fills_idx:
            return

        new_trades = ledger[self._processed_fills_idx:]
        self._processed_fills_idx = len(ledger)

        for trade in new_trades:
            self.metrics.fill_ledger.append(trade)
            sym = trade["symbol"]
            qty = trade["quantity"]
            px = trade["price"]
            fee = trade["fee"]
            is_buy = trade["side"] == "BUY"
            notional = px * qty
            is_maker = trade.get("is_maker", True)

            self.metrics.total_fees_paid += fee
            self.metrics.total_volume_usd += notional
            if is_maker:
                self.metrics.maker_fills += 1
                self.metrics.maker_volume_usd += notional
                self.metrics.maker_fees_paid += fee
            else:
                self.metrics.taker_fills += 1
                self.metrics.taker_volume_usd += notional
                self.metrics.taker_fees_paid += fee

            base_sym = sym.split(":")[-1] if ":" in sym else sym
            ref_mid = mid_prices.get(base_sym, px)
            if ref_mid > 0:
                slip = ((px - ref_mid) if is_buy else (ref_mid - px)) / ref_mid * 10000.0
                self.metrics.slippage_bps.append(slip)

            venue_tag = sym.split(":")[0] if ":" in sym else "UNKNOWN"
            self.metrics.venue_execution_records[venue_tag] += 1
            self.metrics.venue_volume[venue_tag] += notional
            self.metrics.venue_fees[venue_tag] += fee

            cur_pos = self.metrics.positions[base_sym]
            cur_cost = self.avg_costs[base_sym]
            if is_buy:
                if cur_pos < 0:
                    closed = min(abs(cur_pos), qty)
                    self.metrics.realized_pnl += closed * (cur_cost - px)
                new_pos = cur_pos + qty
                if new_pos > 0:
                    self.avg_costs[base_sym] = ((cur_pos * cur_cost) + (qty * px)) / new_pos if cur_pos > 0 else px
                self.metrics.positions[base_sym] = new_pos
                self.metrics.cash -= (notional + fee)
            else:
                if cur_pos > 0:
                    closed = min(cur_pos, qty)
                    self.metrics.realized_pnl += closed * (px - cur_cost)
                new_pos = cur_pos - qty
                if new_pos < 0:
                    self.avg_costs[base_sym] = ((abs(cur_pos) * cur_cost) + (qty * px)) / abs(new_pos) if cur_pos < 0 else px
                self.metrics.positions[base_sym] = new_pos
                self.metrics.cash += (notional - fee)

            cur_abs_inv = abs(self.metrics.positions[base_sym])
            if cur_abs_inv > self.metrics.peak_inventory[base_sym]:
                self.metrics.peak_inventory[base_sym] = cur_abs_inv

            self.risk_engine.update_position(
                symbol=base_sym,
                fill_qty=qty,
                fill_price=px,
                side=trade["side"],
                venue=venue_tag,
            )

        if sync_orders:
            self.sync_order_metrics()

        market_value = sum(
            qty * mid_prices.get(sym, self.avg_costs.get(sym, 0.0))
            for sym, qty in self.metrics.positions.items()
        )
        total_equity = self.metrics.cash + market_value
        self.metrics.equity_curve.append((current_time, total_equity))
        self.risk_engine.update_portfolio_equity(total_equity)


def fast_compute_markouts_summary(ledger, price_history, horizons_ms=[100.0, 1000.0, 5000.0, 10000.0]):
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
            mid_future = mids_list[idx] if idx < len(ts_list) else mids_list[-1]
            markout = direction * (mid_future - mid_t)
            markout_bps = (markout / mid_t) * 10000.0 if mid_t > 0 else 0.0
            results_by_horizon[h].append(markout_bps)

    summary = {}
    for h, vals in results_by_horizon.items():
        summary[f"{int(h)}ms"] = round(sum(vals)/len(vals) if vals else 0.0, 4)
    return summary


def run_inventory_research(
    cap_path: str = "audit/v6.0.4/l2_capture_2hour.jsonl.gz",
    start_frame: int = 1,
    end_frame: int | None = 1925261,
    dataset_label: str = "IN_SAMPLE_DEV",
    out_json_path: str | None = None,
) -> dict[str, Any]:
    """
    Execute controlled multi-variant research on L2 market data.
    """
    t0 = time.time()
    valuator = DenominationAwareValuator()
    reconstructor = L2BookReconstructor()

    strategies = [
        ResearchInventorySkewMM(
            name="A: Baseline Inventory-Skewed",
            penalty_rate=0.04,
            nonlinear_penalty=False,
            asymmetric_widening=False,
            ofi_filter=False,
            ewma_volatility=False,
            toxic_pullback=False,
        ),
        ResearchInventorySkewMM(
            name="B: Nonlinear Penalty Only",
            penalty_rate=0.04,
            nonlinear_penalty=True,
            asymmetric_widening=False,
            ofi_filter=False,
            ewma_volatility=False,
            toxic_pullback=False,
        ),
        ResearchInventorySkewMM(
            name="C: Asymmetric Widening Only",
            penalty_rate=0.04,
            nonlinear_penalty=False,
            asymmetric_widening=True,
            ofi_filter=False,
            ewma_volatility=False,
            toxic_pullback=False,
        ),
        ResearchInventorySkewMM(
            name="D: OFI Filtering Only",
            penalty_rate=0.04,
            nonlinear_penalty=False,
            asymmetric_widening=False,
            ofi_filter=True,
            ewma_volatility=False,
            toxic_pullback=False,
        ),
        ResearchInventorySkewMM(
            name="E: EWMA Vol & Toxic Pullback Only",
            penalty_rate=0.04,
            nonlinear_penalty=False,
            asymmetric_widening=False,
            ofi_filter=False,
            ewma_volatility=True,
            toxic_pullback=True,
        ),
        ResearchInventorySkewMM(
            name="F: Combined Configuration",
            penalty_rate=0.04,
            nonlinear_penalty=True,
            asymmetric_widening=True,
            ofi_filter=True,
            ewma_volatility=True,
            toxic_pullback=True,
        ),
        NoTradeBaseline(initial_capital=100_000.0),
    ]
    strategies[-1].name = "G: No-Trade Baseline"

    current_mids: dict[str, float] = {}
    count = 0
    last_ex_ts = 0.0

    print(f"\n==================================================", flush=True)
    print(f"Starting {dataset_label} Research Run (Frames {start_frame} to {end_frame or 'END'})...", flush=True)
    print(f"==================================================", flush=True)

    for frame in stream_captured_frames(cap_path):
        count += 1
        if end_frame and count > end_frame:
            break

        events = reconstructor.process_frame(frame)
        if count < start_frame:
            continue

        if count % 200000 == 0:
            pct = ((count - start_frame) / ((end_frame or 3683381) - start_frame)) * 100.0
            print(f"Progress: frame {count} ({pct:.1f}%), elapsed {time.time()-t0:.1f}s", flush=True)
        if not events:
            continue

        for ev in events:
            if ev.event_kind == "CONTROL":
                continue
            sym_key = f"{ev.venue}:{ev.instrument}"
            is_usdt = ev.venue in ("BINANCE", "OKX", "BYBIT")
            rate_usdt = valuator.get_fx_rate("USDT")
            fx_mult = rate_usdt if is_usdt else 1.0
            last_ex_ts = max(last_ex_ts, ev.exchange_ts)

            if ev.event_kind in ("BOOK_SNAPSHOT", "BOOK_DELTA") and ev.bids and ev.asks:
                bb_px, bb_sz = ev.bids[0]
                ba_px, ba_sz = ev.asks[0]
                bb_usd = bb_px * fx_mult
                ba_usd = ba_px * fx_mult
                mid_usd = (bb_usd + ba_usd) / 2.0
                current_mids[ev.instrument] = mid_usd
                current_mids[sym_key] = mid_usd

                for strat in strategies:
                    strat.engine.update_book(sym_key, [(bb_usd, bb_sz)], [(ba_usd, ba_sz)], ev.exchange_ts)
                    strat.on_validated_quote(ev.venue, ev.instrument, bb_usd, ba_usd, bb_sz, ba_sz, ev.exchange_ts)
                    strat.engine.process_time_slice(ev.exchange_ts)

            elif ev.event_kind == "TRADE" and ev.price is not None and ev.quantity is not None and not ev.duplicate:
                tr_px_usd = ev.price * fx_mult
                agg_side_str = ev.aggressor_side.value if ev.aggressor_side else None
                for strat in strategies:
                    if hasattr(strat, "on_validated_trade"):
                        strat.on_validated_trade(ev.venue, ev.instrument, tr_px_usd, ev.quantity, agg_side_str, ev.exchange_ts)
                    strat.engine.process_market_trade(
                        symbol=sym_key,
                        trade_price=tr_px_usd,
                        trade_size=ev.quantity,
                        timestamp=ev.exchange_ts,
                        aggressor_side=ev.aggressor_side,
                    )
                    strat.sync_fills(ev.exchange_ts, current_mids)

    print(f"Simulation completed in {time.time()-t0:.2f}s! Collating results...", flush=True)

    results: dict[str, Any] = {
        "dataset_label": dataset_label,
        "frames_processed": count - start_frame + 1,
        "elapsed_wall_s": round(time.time() - t0, 2),
        "strategies": {},
    }

    for strat in strategies:
        strat.sync_order_metrics()
        strat.sync_fills(last_ex_ts, current_mids)
        m = strat.metrics
        mkt_val = sum(
            qty * current_mids.get(sym, strat.avg_costs.get(sym, 0.0))
            for sym, qty in m.positions.items()
        )
        final_equity = m.cash + mkt_val
        net_pnl = final_equity - strat.initial_capital
        gross_pnl = net_pnl + m.total_fees_paid
        tot_fills = m.maker_fills + m.taker_fills
        maker_pct = (m.maker_fills / tot_fills * 100.0) if tot_fills > 0 else 0.0
        fill_prob = (tot_fills / m.orders_submitted * 100.0) if m.orders_submitted > 0 else 0.0

        peak_eq = strat.initial_capital
        max_dd_usd = 0.0
        max_dd_pct = 0.0
        for _, eq in m.equity_curve:
            if eq > peak_eq:
                peak_eq = eq
            dd_usd = peak_eq - eq
            dd_pct = (dd_usd / peak_eq * 100.0) if peak_eq > 0 else 0.0
            if dd_usd > max_dd_usd:
                max_dd_usd = dd_usd
            if dd_pct > max_dd_pct:
                max_dd_pct = dd_pct

        ledger = strat.engine.get_ledger()
        markouts = fast_compute_markouts_summary(ledger, strat.engine._price_history) if ledger else {}

        # Instrument and venue breakdowns
        btc_fills = len([t for t in ledger if "BTC" in t["symbol"]])
        eth_fills = len([t for t in ledger if "ETH" in t["symbol"]])
        venue_execs = dict(m.venue_execution_records)

        res_entry = {
            "name": strat.name,
            "orders_submitted": m.orders_submitted,
            "orders_rejected": m.orders_rejected,
            "total_fills": tot_fills,
            "maker_fills": m.maker_fills,
            "taker_fills": m.taker_fills,
            "maker_fill_pct": round(maker_pct, 2),
            "fill_probability_pct": round(fill_prob, 2),
            "gross_pnl_usd": round(gross_pnl, 4),
            "total_fees_usd": round(m.total_fees_paid, 4),
            "net_pnl_usd": round(net_pnl, 4),
            "final_equity_usd": round(final_equity, 2),
            "max_drawdown_usd": round(max_dd_usd, 4),
            "max_drawdown_pct": round(max_dd_pct, 4),
            "peak_inventory_btc": round(m.peak_inventory.get("BTC/USD", 0.0), 6),
            "peak_inventory_eth": round(m.peak_inventory.get("ETH/USD", 0.0), 6),
            "final_position_btc": round(m.positions.get("BTC/USD", 0.0), 6),
            "final_position_eth": round(m.positions.get("ETH/USD", 0.0), 6),
            "markouts": markouts,
            "instrument_fills": {"BTC": btc_fills, "ETH": eth_fills},
            "venue_executions": venue_execs,
        }
        results["strategies"][strat.name] = res_entry

        print(f"\n--- {strat.name} ---")
        print(f"  Fills: {tot_fills} (Maker%: {maker_pct:.1f}%), Fill Prob: {fill_prob:.2f}%")
        print(f"  Gross PnL: ${gross_pnl:+.2f}, Fees: ${m.total_fees_paid:.2f}, Net PnL: ${net_pnl:+.2f}")
        print(f"  Max DD: ${max_dd_usd:.2f} ({max_dd_pct:.2f}%), End Pos: {m.positions.get('BTC/USD', 0.0):+.4f} BTC, {m.positions.get('ETH/USD', 0.0):+.4f} ETH")
        if markouts:
            print(f"  Markouts (bps): 100ms={markouts.get('100ms', 0):+.3f}, 1s={markouts.get('1000ms', 0):+.3f}, 5s={markouts.get('5000ms', 0):+.3f}, 10s={markouts.get('10000ms', 0):+.3f}")

    if out_json_path:
        os.makedirs(os.path.dirname(out_json_path), exist_ok=True)
        with open(out_json_path, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nResults successfully written to: {out_json_path}", flush=True)

    return results

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="MDRAP Inventory Skew Research Runner")
    parser.add_argument("--mode", choices=["in_sample", "holdout", "smoke"], default="smoke")
    parser.add_argument("--cap", default="audit/v6.0.4/l2_capture_2hour.jsonl.gz")
    args = parser.parse_args()

    if args.mode == "smoke":
        run_inventory_research(
            cap_path=args.cap,
            start_frame=1,
            end_frame=30000,
            dataset_label="SMOKE_TEST",
            out_json_path="audit/v6.0.4.1/research_smoke.json",
        )
    elif args.mode == "in_sample":
        run_inventory_research(
            cap_path=args.cap,
            start_frame=1,
            end_frame=1925261,
            dataset_label="IN_SAMPLE_DEV_1H",
            out_json_path="audit/v6.0.4.1/research_in_sample.json",
        )
    elif args.mode == "holdout":
        run_inventory_research(
            cap_path=args.cap,
            start_frame=1925262,
            end_frame=None,
            dataset_label="OUT_OF_SAMPLE_HOLDOUT_1H",
            out_json_path="audit/v6.0.4.1/research_holdout.json",
        )
