#!/usr/bin/env python3
"""
MDRAP 6.0.5 — Directional Alpha Research Harness

Evaluates candidate directional predictive signals from genuine L2 market data:
1. Order Flow Imbalance (OFI)
2. Signed Trade Imbalance (STI)
3. Microprice Displacement (Stoikov)
4. Short-Term Momentum (100ms, 1s, 5s)
5. EWMA Volatility-Signed Momentum
6. Consecutive Aggressive Trade Runs
7. Bid/Ask Depth Imbalance

Predictive horizons: 100ms, 500ms, 1s, 5s, 10s.
Chronological partitions:
- Development (DEV):      0 to 2,700s (0 to 45 min)
- Validation (VAL):    2,700 to 4,800s (45 to 80 min)
- Holdout (HOLDOUT):   4,800 to 7,209s (80 to 120 min)

Accounting for 2.5ms execution latency, spread crossing, and maker/taker fees.
"""

import bisect
import collections
import gzip
import json
import math
import os
import sys
import time
from typing import Any

import numpy as np
from scipy import stats

sys.path.insert(0, "src")
sys.path.insert(0, "tools")
sys.path.insert(0, "packages/mdrap-strategies")

from replay_l2 import stream_captured_frames, L2BookReconstructor
from mdrap_strategies.fx_basis import DenominationAwareValuator


def run_directional_alpha_research(
    cap_path: str = "audit/v6.0.4/l2_capture_2hour.jsonl.gz",
    sample_interval_s: float = 0.50,
    output_json: str = "audit/v6.0.5/directional_alpha_results.json",
):
    print("=" * 70, flush=True)
    print("MDRAP 6.0.5 — Directional Alpha Research", flush=True)
    print(f"Data Source: {cap_path}", flush=True)
    print(f"Sampling Interval: {sample_interval_s * 1000:.0f} ms", flush=True)
    print("=" * 70, flush=True)

    reconstructor = L2BookReconstructor()
    valuator = DenominationAwareValuator()

    # Per-stream state tracking
    # Stream key: "VENUE:INSTRUMENT" e.g. "BINANCE:BTC/USD"
    mid_hist_ts: dict[str, list[float]] = collections.defaultdict(list)
    mid_hist_px: dict[str, list[float]] = collections.defaultdict(list)

    # Rolling indicators
    prev_bbo: dict[str, tuple[float, float, float, float]] = {}  # bid, ask, bsz, asz
    ofi_ema: dict[str, float] = collections.defaultdict(float)
    ofi_vol_ema: dict[str, float] = collections.defaultdict(lambda: 1.0)
    sti_ema: dict[str, float] = collections.defaultdict(float)
    sti_vol_ema: dict[str, float] = collections.defaultdict(lambda: 1.0)
    ewma_var: dict[str, float] = collections.defaultdict(lambda: 2.5e-7)  # (0.0005)^2
    prev_mid: dict[str, float] = {}
    trade_runs: dict[str, tuple[int, int]] = {}  # (side_sign: +1/-1, count)
    last_sample_t: dict[str, float] = collections.defaultdict(float)

    # Observation records: list of dicts
    samples: list[dict[str, Any]] = []

    t0 = time.time()
    count = 0
    first_wall_s: float | None = None
    last_wall_s: float = 0.0

    print(
        "Pass 1: Streaming frames, maintaining L2 order books & sampling signals...",
        flush=True,
    )

    for frame in stream_captured_frames(cap_path):
        count += 1
        wall_s = frame.get("recv_wall_s", 0.0)
        if first_wall_s is None and wall_s > 0:
            first_wall_s = wall_s
        last_wall_s = wall_s

        if count % 500000 == 0:
            elapsed_m = (wall_s - (first_wall_s or wall_s)) / 60.0
            print(
                f"  Frame {count:7d} | Replay time: {elapsed_m:5.1f} min | Wall elapsed: {time.time() - t0:.1f}s | Samples: {len(samples)}",
                flush=True,
            )

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
            ts = ev.exchange_ts

            if ev.event_kind in ("BOOK_SNAPSHOT", "BOOK_DELTA") and ev.bids and ev.asks:
                bb_px, bb_sz = ev.bids[0]
                ba_px, ba_sz = ev.asks[0]
                bb_usd = bb_px * fx_mult
                ba_usd = ba_px * fx_mult

                if ba_usd <= bb_usd:
                    continue  # Crossed/locked book anomaly, skip update

                mid_usd = (bb_usd + ba_usd) / 2.0
                spread_bps = ((ba_usd - bb_usd) / mid_usd) * 10000.0
                half_spread_bps = spread_bps / 2.0

                # Append mid-price history (monotonically non-decreasing timestamps)
                ts_list = mid_hist_ts[sym_key]
                if not ts_list or ts >= ts_list[-1]:
                    ts_list.append(ts)
                    mid_hist_px[sym_key].append(mid_usd)

                # 1. Update OFI
                if sym_key in prev_bbo:
                    p_b0, p_a0, q_b0, q_a0 = prev_bbo[sym_key]
                    delta_b = (
                        bb_sz
                        if bb_usd > p_b0
                        else ((bb_sz - q_b0) if bb_usd == p_b0 else -q_b0)
                    )
                    delta_a = (
                        -ba_sz
                        if ba_usd < p_a0
                        else ((ba_usd - q_a0) if ba_usd == p_a0 else q_a0)
                    )
                    raw_ofi = delta_b - delta_a
                    vol_delta = abs(delta_b) + abs(delta_a) + 1e-6
                    ofi_ema[sym_key] = 0.90 * ofi_ema[sym_key] + 0.10 * raw_ofi
                    ofi_vol_ema[sym_key] = (
                        0.90 * ofi_vol_ema[sym_key] + 0.10 * vol_delta
                    )

                prev_bbo[sym_key] = (bb_usd, ba_usd, bb_sz, ba_sz)

                # 2. Update EWMA Volatility
                if sym_key in prev_mid and prev_mid[sym_key] > 0:
                    ret = (mid_usd - prev_mid[sym_key]) / prev_mid[sym_key]
                    ewma_var[sym_key] = 0.95 * ewma_var[sym_key] + 0.05 * (ret * ret)
                prev_mid[sym_key] = mid_usd

                # Periodic sampling
                if ts - last_sample_t[sym_key] >= sample_interval_s:
                    last_sample_t[sym_key] = ts
                    elapsed_total = wall_s - (first_wall_s or wall_s)

                    if elapsed_total < 2700.0:
                        partition = "DEV"
                    elif elapsed_total < 4800.0:
                        partition = "VAL"
                    else:
                        partition = "HOLDOUT"

                    # Calculate signal features
                    # Signal 1: OFI
                    s_ofi = (
                        ofi_ema[sym_key] / ofi_vol_ema[sym_key]
                        if ofi_vol_ema[sym_key] > 0
                        else 0.0
                    )
                    s_ofi = max(-1.0, min(1.0, s_ofi))

                    # Signal 2: STI
                    s_sti = (
                        sti_ema[sym_key] / sti_vol_ema[sym_key]
                        if sti_vol_ema[sym_key] > 0
                        else 0.0
                    )
                    s_sti = max(-1.0, min(1.0, s_sti))

                    # Signal 3: Microprice Displacement (bps)
                    micro_px = (
                        (ba_usd * bb_sz + bb_usd * ba_sz) / (bb_sz + ba_sz)
                        if (bb_sz + ba_sz) > 0
                        else mid_usd
                    )
                    s_micro = ((micro_px - mid_usd) / mid_usd) * 10000.0

                    # Signal 4: Depth Imbalance
                    s_depth = (
                        (bb_sz - ba_sz) / (bb_sz + ba_sz)
                        if (bb_sz + ba_sz) > 0
                        else 0.0
                    )

                    # Signal 5: Short-Term Momentum (100ms, 1s, 5s)
                    def get_past_mid(dt_sec: float) -> float:
                        idx = bisect.bisect_right(ts_list, ts - dt_sec)
                        if idx > 0:
                            return mid_hist_px[sym_key][idx - 1]
                        return mid_hist_px[sym_key][0]

                    mid_100ms = get_past_mid(0.10)
                    mid_1s = get_past_mid(1.0)
                    mid_5s = get_past_mid(5.0)

                    s_mom_100ms = (
                        ((mid_usd - mid_100ms) / mid_100ms) * 10000.0
                        if mid_100ms > 0
                        else 0.0
                    )
                    s_mom_1s = (
                        ((mid_usd - mid_1s) / mid_1s) * 10000.0 if mid_1s > 0 else 0.0
                    )
                    s_mom_5s = (
                        ((mid_usd - mid_5s) / mid_5s) * 10000.0 if mid_5s > 0 else 0.0
                    )

                    # Signal 6: EWMA Volatility (bps)
                    vol_bps = math.sqrt(ewma_var[sym_key]) * 10000.0
                    s_vol_mom = (1.0 if s_mom_1s >= 0 else -1.0) * vol_bps

                    # Signal 7: Trade Runs
                    r_side, r_count = trade_runs.get(sym_key, (0, 0))
                    s_runs = float(r_side * r_count)

                    samples.append(
                        {
                            "sym_key": sym_key,
                            "venue": ev.venue,
                            "instrument": ev.instrument,
                            "partition": partition,
                            "ts": ts,
                            "mid": mid_usd,
                            "half_spread_bps": half_spread_bps,
                            "vol_bps": vol_bps,
                            "s_ofi": s_ofi,
                            "s_sti": s_sti,
                            "s_micro": s_micro,
                            "s_depth": s_depth,
                            "s_mom_100ms": s_mom_100ms,
                            "s_mom_1s": s_mom_1s,
                            "s_mom_5s": s_mom_5s,
                            "s_vol_mom": s_vol_mom,
                            "s_runs": s_runs,
                        }
                    )

            elif (
                ev.event_kind == "TRADE"
                and ev.price is not None
                and ev.quantity is not None
                and not ev.duplicate
            ):
                # Update STI
                agg_sign = 0
                if ev.aggressor_side:
                    agg_sign = 1 if ev.aggressor_side.value == "BUY" else -1

                sti_ema[sym_key] = 0.90 * sti_ema[sym_key] + 0.10 * (
                    agg_sign * ev.quantity
                )
                sti_vol_ema[sym_key] = 0.90 * sti_vol_ema[sym_key] + 0.10 * (
                    ev.quantity + 1e-6
                )

                # Update Consecutive Runs
                if agg_sign != 0:
                    prev_side, prev_cnt = trade_runs.get(sym_key, (0, 0))
                    if agg_sign == prev_side:
                        trade_runs[sym_key] = (agg_sign, prev_cnt + 1)
                    else:
                        trade_runs[sym_key] = (agg_sign, 1)

    print(f"\nPass 1 Complete! Processed {count} frames in {time.time() - t0:.1f}s.")
    print(f"Total Observation Samples Collected: {len(samples)}", flush=True)

    # Pass 2: Calculate future forward returns
    print(
        "\nPass 2: Computing forward returns at [100ms, 500ms, 1s, 5s, 10s] with 2.5ms latency...",
        flush=True,
    )
    horizons = [0.10, 0.50, 1.0, 5.0, 10.0]
    latency_s = 0.0025  # 2.5ms execution latency (2.0ms wire + 0.5ms engine)

    for samp in samples:
        sym = samp["sym_key"]
        ts_now = samp["ts"]
        ts_arr = mid_hist_ts[sym]
        px_arr = mid_hist_px[sym]

        # Execution price at t + 2.5ms
        idx_exec = bisect.bisect_left(ts_arr, ts_now + latency_s)
        px_exec = px_arr[idx_exec] if idx_exec < len(px_arr) else px_arr[-1]
        samp["px_exec"] = px_exec

        # Forward prices
        for h in horizons:
            h_key = f"ret_{int(h * 1000)}ms"
            idx_fwd = bisect.bisect_left(ts_arr, ts_now + latency_s + h)
            px_fwd = px_arr[idx_fwd] if idx_fwd < len(px_arr) else px_arr[-1]
            ret_bps = ((px_fwd - px_exec) / px_exec) * 10000.0 if px_exec > 0 else 0.0
            samp[h_key] = ret_bps

    print(
        "Pass 2 Complete! Running Statistical and Economic Validations...", flush=True
    )

    # Candidate signals list
    signal_keys = [
        ("s_ofi", "Order Flow Imbalance (OFI)", 0.25),
        ("s_sti", "Signed Trade Imbalance (STI)", 0.25),
        ("s_micro", "Microprice Displacement", 0.50),  # > 0.5 bps
        ("s_depth", "Bid/Ask Depth Imbalance", 0.25),
        ("s_mom_100ms", "Short-Term Momentum (100ms)", 0.50),
        ("s_mom_1s", "Short-Term Momentum (1s)", 1.00),
        ("s_mom_5s", "Short-Term Momentum (5s)", 2.00),
        ("s_vol_mom", "EWMA Vol-Signed Momentum", 1.00),
        ("s_runs", "Aggressor Trade Runs (>=3)", 3.00),
    ]

    results: dict[str, Any] = {
        "metadata": {
            "total_frames": count,
            "total_samples": len(samples),
            "sample_interval_s": sample_interval_s,
            "latency_s": latency_s,
            "partitions": {
                "DEV": sum(1 for s in samples if s["partition"] == "DEV"),
                "VAL": sum(1 for s in samples if s["partition"] == "VAL"),
                "HOLDOUT": sum(1 for s in samples if s["partition"] == "HOLDOUT"),
            },
        },
        "signals": {},
        "holdout_matrix": {},
        "economic_eval": {},
        "regime_eval": {},
        "exchange_eval": {},
        "asset_eval": {},
    }

    # Statistical Evaluation across Partitions
    for sig_key, sig_name, threshold in signal_keys:
        results["signals"][sig_name] = {}
        for part in ("DEV", "VAL", "HOLDOUT"):
            part_samples = [s for s in samples if s["partition"] == part]
            if not part_samples:
                continue

            results["signals"][sig_name][part] = {}
            for h in horizons:
                h_str = f"{int(h * 1000)}ms"
                ret_key = f"ret_{int(h * 1000)}ms"

                sig_vals = np.array([s[sig_key] for s in part_samples])
                ret_vals = np.array([s[ret_key] for s in part_samples])

                # Pearson IC
                if np.std(sig_vals) > 1e-9 and np.std(ret_vals) > 1e-9:
                    pearson_ic, p_val = stats.pearsonr(sig_vals, ret_vals)
                    spearman_ic, sp_val = stats.spearmanr(sig_vals, ret_vals)
                else:
                    pearson_ic, p_val, spearman_ic, sp_val = 0.0, 1.0, 0.0, 1.0

                # Directional Accuracy (Hit Rate) on active signals (|S| >= threshold)
                active_idx = np.where(np.abs(sig_vals) >= threshold)[0]
                if len(active_idx) > 0:
                    act_sig = sig_vals[active_idx]
                    act_ret = ret_vals[active_idx]
                    hits = np.sum(np.sign(act_sig) == np.sign(act_ret))
                    non_zero = np.sum(act_ret != 0)
                    hit_rate = (hits / non_zero * 100.0) if non_zero > 0 else 50.0

                    # Directional return (bps)
                    dir_rets = np.sign(act_sig) * act_ret
                    mean_ret = float(np.mean(dir_rets))
                    std_ret = float(np.std(dir_rets))
                    se_ret = (
                        std_ret / math.sqrt(len(dir_rets)) if len(dir_rets) > 0 else 0.0
                    )
                    ci95 = 1.96 * se_ret
                    t_stat = (mean_ret / se_ret) if se_ret > 0 else 0.0
                else:
                    hit_rate, mean_ret, ci95, t_stat, len_active = (
                        50.0,
                        0.0,
                        0.0,
                        0.0,
                        0,
                    )

                results["signals"][sig_name][part][h_str] = {
                    "pearson_ic": round(float(pearson_ic), 4),
                    "spearman_ic": round(float(spearman_ic), 4),
                    "hit_rate_pct": round(float(hit_rate), 2),
                    "mean_return_bps": round(float(mean_ret), 4),
                    "ci95_bps": round(float(ci95), 4),
                    "t_stat": round(float(t_stat), 2),
                    "active_samples": int(len(active_idx)),
                    "total_samples": len(part_samples),
                }

    # Economic Evaluation on HOLDOUT dataset
    # Measure expected return after realistic taker and maker costs
    holdout_samples = [s for s in samples if s["partition"] == "HOLDOUT"]
    avg_half_spread = np.mean([s["half_spread_bps"] for s in holdout_samples])

    results["economic_eval"] = {
        "holdout_samples": len(holdout_samples),
        "avg_half_spread_bps": round(float(avg_half_spread), 4),
        "cost_assumptions": {
            "standard_taker_fee_bps": 6.0,
            "vip_taker_fee_bps": 2.0,
            "maker_fee_bps": 1.0,
            "execution_latency_ms": 2.5,
        },
        "signals_holdout_1s": {},
        "signals_holdout_5s": {},
    }

    for sig_key, sig_name, threshold in signal_keys:
        for target_h, h_key, res_dict in [
            (1.0, "ret_1000ms", results["economic_eval"]["signals_holdout_1s"]),
            (5.0, "ret_5000ms", results["economic_eval"]["signals_holdout_5s"]),
        ]:
            sig_vals = np.array([s[sig_key] for s in holdout_samples])
            ret_vals = np.array([s[h_key] for s in holdout_samples])
            half_spreads = np.array([s["half_spread_bps"] for s in holdout_samples])

            active_idx = np.where(np.abs(sig_vals) >= threshold)[0]
            if len(active_idx) > 0:
                act_sig = sig_vals[active_idx]
                act_ret = ret_vals[active_idx]
                act_sp = half_spreads[active_idx]

                dir_rets = np.sign(act_sig) * act_ret
                gross_mean = float(np.mean(dir_rets))
                mean_sp = float(np.mean(act_sp))

                # Taker Model: Cross spread on entry and exit, pay taker fees
                # Round-trip cost = 2 * half_spread + 2 * fee
                net_taker_std = gross_mean - (2 * mean_sp) - (2 * 6.0)
                net_taker_vip = gross_mean - (2 * mean_sp) - (2 * 2.0)

                # Maker Quoting Model: Post limit order on predicted side
                # Gross edge = spread capture + directional drift - maker fee
                # In baseline, adverse selection was -1.13 bps. If signal has directional drift, it offsets it.
                net_maker_edge = mean_sp + gross_mean - 1.0

                res_dict[sig_name] = {
                    "triggers": int(len(active_idx)),
                    "trigger_pct": round(
                        len(active_idx) / len(holdout_samples) * 100.0, 2
                    ),
                    "gross_return_bps": round(gross_mean, 4),
                    "half_spread_bps": round(mean_sp, 4),
                    "net_taker_standard_bps": round(net_taker_std, 4),
                    "net_taker_vip_bps": round(net_taker_vip, 4),
                    "net_maker_edge_bps": round(net_maker_edge, 4),
                    "profitable_taker": bool(net_taker_vip > 0),
                    "profitable_maker": bool(net_maker_edge > 0),
                }

    # Breakdown by Exchange (Holdout, 1s horizon)
    for venue in ("BINANCE", "COINBASE", "KRAKEN", "OKX", "BYBIT"):
        v_samples = [s for s in holdout_samples if s["venue"] == venue]
        results["exchange_eval"][venue] = {"samples": len(v_samples)}
        for sig_key, sig_name, threshold in [
            ("s_ofi", "OFI", 0.25),
            ("s_micro", "Microprice", 0.50),
            ("s_mom_1s", "Mom_1s", 1.0),
        ]:
            sig_vals = np.array([s[sig_key] for s in v_samples])
            ret_vals = np.array([s["ret_1000ms"] for s in v_samples])
            active_idx = np.where(np.abs(sig_vals) >= threshold)[0]
            if len(active_idx) > 10:
                ic, _ = stats.spearmanr(sig_vals, ret_vals)
                dir_ret = np.mean(np.sign(sig_vals[active_idx]) * ret_vals[active_idx])
                results["exchange_eval"][venue][sig_name] = {
                    "spearman_ic": round(float(ic), 4),
                    "mean_return_bps": round(float(dir_ret), 4),
                    "triggers": len(active_idx),
                }

    # Breakdown by Asset (Holdout, 1s horizon)
    for inst in ("BTC/USD", "ETH/USD"):
        a_samples = [s for s in holdout_samples if s["instrument"] == inst]
        results["asset_eval"][inst] = {"samples": len(a_samples)}
        for sig_key, sig_name, threshold in [
            ("s_ofi", "OFI", 0.25),
            ("s_micro", "Microprice", 0.50),
            ("s_mom_1s", "Mom_1s", 1.0),
        ]:
            sig_vals = np.array([s[sig_key] for s in a_samples])
            ret_vals = np.array([s["ret_1000ms"] for s in a_samples])
            active_idx = np.where(np.abs(sig_vals) >= threshold)[0]
            if len(active_idx) > 10:
                ic, _ = stats.spearmanr(sig_vals, ret_vals)
                dir_ret = np.mean(np.sign(sig_vals[active_idx]) * ret_vals[active_idx])
                results["asset_eval"][inst][sig_name] = {
                    "spearman_ic": round(float(ic), 4),
                    "mean_return_bps": round(float(dir_ret), 4),
                    "triggers": len(active_idx),
                }

    # Breakdown by Volatility Regime (Holdout, 1s horizon)
    vol_vals = np.array([s["vol_bps"] for s in holdout_samples])
    q33, q66 = np.percentile(vol_vals, 33.3), np.percentile(vol_vals, 66.6)
    for reg_name, filter_fn in [
        ("Low_Vol", lambda s: s["vol_bps"] <= q33),
        ("Med_Vol", lambda s: q33 < s["vol_bps"] <= q66),
        ("High_Vol", lambda s: s["vol_bps"] > q66),
    ]:
        r_samples = [s for s in holdout_samples if filter_fn(s)]
        results["regime_eval"][reg_name] = {"samples": len(r_samples)}
        for sig_key, sig_name, threshold in [
            ("s_ofi", "OFI", 0.25),
            ("s_micro", "Microprice", 0.50),
            ("s_mom_1s", "Mom_1s", 1.0),
        ]:
            sig_vals = np.array([s[sig_key] for s in r_samples])
            ret_vals = np.array([s["ret_1000ms"] for s in r_samples])
            active_idx = np.where(np.abs(sig_vals) >= threshold)[0]
            if len(active_idx) > 10:
                ic, _ = stats.spearmanr(sig_vals, ret_vals)
                dir_ret = np.mean(np.sign(sig_vals[active_idx]) * ret_vals[active_idx])
                results["regime_eval"][reg_name][sig_name] = {
                    "spearman_ic": round(float(ic), 4),
                    "mean_return_bps": round(float(dir_ret), 4),
                    "triggers": len(active_idx),
                }

    # Save output
    os.makedirs(os.path.dirname(output_json), exist_ok=True)
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print(f"\nResults successfully written to: {output_json}", flush=True)

    # Print Holdout Scorecard
    print("\n" + "=" * 90, flush=True)
    print(
        "HOLDOUT OUT-OF-SAMPLE DIRECTIONAL ALPHA SCORECARD (1-Second Horizon)",
        flush=True,
    )
    print("=" * 90, flush=True)
    print(
        f"{'Signal':<32} | {'Spearman IC':<11} | {'Hit Rate':<9} | {'Gross (bps)':<11} | {'95% CI':<9} | {'Taker VIP':<10} | {'Maker Net':<10}",
        flush=True,
    )
    print("-" * 90, flush=True)

    for _, sig_name, _ in signal_keys:
        h1s = results["signals"][sig_name]["HOLDOUT"]["1000ms"]
        econ = results["economic_eval"]["signals_holdout_1s"].get(sig_name, {})
        print(
            f"{sig_name:<32} | {h1s['spearman_ic']:>+11.4f} | {h1s['hit_rate_pct']:>8.2f}% | "
            f"{h1s['mean_return_bps']:>+10.4f} | {h1s['ci95_bps']:>8.4f} | "
            f"{econ.get('net_taker_vip_bps', 0.0):>+9.4f} | {econ.get('net_maker_edge_bps', 0.0):>+9.4f}",
            flush=True,
        )
    print("=" * 90, flush=True)
    return results


if __name__ == "__main__":
    run_directional_alpha_research()
