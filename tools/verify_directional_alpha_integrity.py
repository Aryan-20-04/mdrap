#!/usr/bin/env python3
"""
MDRAP 6.0.5 — Statistical Integrity Verification Tool

Independently checks:
1. Serial correlation in forward returns (ACF rho_1 .. rho_10)
2. Overlapping observation effects (MA(q) induced dependency)
3. Newey-West (1987) Heteroskedasticity and Autocorrelation Consistent (HAC) standard errors
4. Moving Block Bootstrap (Politis & Romano 1994) confidence intervals (B=1000)
5. Multiple-testing bias corrections:
   - Bonferroni FWER adjustment
   - Holm-Bonferroni step-down
   - Benjamini-Hochberg (1995) False Discovery Rate (FDR)
6. Effective Sample Size (N_eff) via Bartlett / Newey-West kernel
"""

import bisect
import json
import math
import sys
import numpy as np
from scipy import stats

sys.path.insert(0, "src")
sys.path.insert(0, "tools")
sys.path.insert(0, "packages/mdrap-strategies")

from replay_l2 import stream_captured_frames, L2BookReconstructor
from mdrap_strategies.fx_basis import DenominationAwareValuator


def run_statistical_integrity_verification(
    cap_path: str = "audit/v6.0.4/l2_capture_2hour.jsonl.gz",
    output_json: str = "audit/v6.0.5/statistical_integrity_audit.json",
):
    print("=" * 80)
    print("MDRAP 6.0.5 — Statistical Integrity & Econometric Robustness Verification")
    print(f"Data Source: {cap_path}")
    print("=" * 80)

    # Load existing results to get metadata
    with open("audit/v6.0.5/directional_alpha_results.json", "r") as f:
        alpha_res = json.load(f)

    # 1. Multiplicity Corrections across all 45 Signal-Horizon Hypotheses (Holdout)
    signals = list(alpha_res["signals"].keys())
    horizons = ["100ms", "500ms", "1000ms", "5000ms", "10000ms"]
    m = len(signals) * len(horizons)  # 9 * 5 = 45 tests

    p_val_records = []
    for sig in signals:
        for h in horizons:
            h_data = alpha_res["signals"][sig]["HOLDOUT"][h]
            t = h_data["t_stat"]
            # Two-tailed p-value from t-stat
            p_val = 2.0 * (1.0 - stats.norm.cdf(abs(t))) if abs(t) > 0 else 1.0
            p_val_records.append(
                {
                    "signal": sig,
                    "horizon": h,
                    "spearman_ic": h_data["spearman_ic"],
                    "mean_return_bps": h_data["mean_return_bps"],
                    "t_stat": t,
                    "p_val_raw": float(p_val),
                }
            )

    # Sort for Benjamini-Hochberg and Holm-Bonferroni
    p_val_records.sort(key=lambda x: x["p_val_raw"])

    # Bonferroni alpha = 0.05 / 45 = 0.00111
    alpha_fwer = 0.05
    bonf_alpha = alpha_fwer / m

    for rank, rec in enumerate(p_val_records, 1):
        raw_p = rec["p_val_raw"]
        # Bonferroni adjusted p
        rec["p_bonferroni"] = min(1.0, raw_p * m)
        rec["sig_bonferroni"] = bool(raw_p <= bonf_alpha)

        # Holm-Bonferroni threshold = alpha / (m - rank + 1)
        holm_thresh = alpha_fwer / (m - rank + 1)
        rec["p_holm"] = min(1.0, raw_p * (m - rank + 1))
        rec["sig_holm"] = bool(raw_p <= holm_thresh)

        # Benjamini-Hochberg threshold = (rank / m) * FDR_q (Q = 0.05)
        bh_thresh = (rank / m) * alpha_fwer
        rec["bh_threshold"] = bh_thresh
        rec["sig_bh_fdr"] = bool(raw_p <= bh_thresh)

    print(f"\n--- 1. Multiple-Testing Correction Audit ({m} hypotheses tested) ---")
    print(
        f"{'Signal':<30} | {'Horiz':<7} | {'t-stat':<7} | {'Raw p-val':<11} | {'Bonferroni p':<12} | {'FDR Sig?':<8}"
    )
    print("-" * 85)
    for rec in p_val_records[:15]:
        p_str = f"{rec['p_val_raw']:.2e}" if rec["p_val_raw"] > 0 else "< 1e-100"
        bonf_str = (
            f"{rec['p_bonferroni']:.2e}" if rec["p_bonferroni"] > 0 else "< 1e-100"
        )
        fdr_sig = "YES (p<Q)" if rec["sig_bh_fdr"] else "NO"
        print(
            f"{rec['signal'][:30]:<30} | {rec['horizon']:<7} | {rec['t_stat']:>6.2f}  | {p_str:<11} | {bonf_str:<12} | {fdr_sig}"
        )

    # 2. Extract Holdout Samples for Depth Imbalance & Momentum 100ms for HAC and Block Bootstrap
    print(
        "\n--- 2. Streaming Holdout Frames for Autocorrelation, HAC & Block Bootstrap ---"
    )
    reconstructor = L2BookReconstructor()
    valuator = DenominationAwareValuator()

    mid_hist_ts = []
    mid_hist_px = []
    sample_records = []

    count = 0
    first_wall_s = None
    last_sample_t = 0.0

    for frame in stream_captured_frames(cap_path):
        count += 1
        wall_s = frame.get("recv_wall_s", 0.0)
        if first_wall_s is None and wall_s > 0:
            first_wall_s = wall_s

        elapsed = wall_s - (first_wall_s or wall_s)
        # Fast forward to Holdout partition (elapsed >= 4800s)
        events = reconstructor.process_frame(frame)
        if elapsed < 4800.0:
            continue

        for ev in events:
            if (
                ev.event_kind == "CONTROL"
                or ev.venue != "BINANCE"
                or ev.instrument != "BTC/USD"
            ):
                continue

            rate = valuator.get_fx_rate("USDT")
            ts = ev.exchange_ts

            if ev.event_kind in ("BOOK_SNAPSHOT", "BOOK_DELTA") and ev.bids and ev.asks:
                bb_px, bb_sz = ev.bids[0]
                ba_px, ba_sz = ev.asks[0]
                bb_usd = bb_px * rate
                ba_usd = ba_px * rate
                if ba_usd <= bb_usd:
                    continue
                mid = (bb_usd + ba_usd) / 2.0
                if not mid_hist_ts or ts >= mid_hist_ts[-1]:
                    mid_hist_ts.append(ts)
                    mid_hist_px.append(mid)

                if ts - last_sample_t >= 0.50:
                    last_sample_t = ts
                    s_depth = (bb_sz - ba_sz) / (bb_sz + ba_sz)
                    sample_records.append(
                        {
                            "ts": ts,
                            "mid": mid,
                            "s_depth": s_depth,
                        }
                    )

    print(
        f"Collected {len(sample_records)} high-resolution Holdout samples on BINANCE:BTC/USD."
    )

    # Calculate returns at 1s and 5s
    lat = 0.0025
    for s in sample_records:
        t_exec = s["ts"] + lat
        idx_e = bisect.bisect_left(mid_hist_ts, t_exec)
        px_e = mid_hist_px[idx_e] if idx_e < len(mid_hist_px) else mid_hist_px[-1]

        # 1s return
        idx_1s = bisect.bisect_left(mid_hist_ts, t_exec + 1.0)
        px_1s = mid_hist_px[idx_1s] if idx_1s < len(mid_hist_px) else mid_hist_px[-1]
        s["ret_1s"] = ((px_1s - px_e) / px_e) * 10000.0 if px_e > 0 else 0.0

        # 5s return
        idx_5s = bisect.bisect_left(mid_hist_ts, t_exec + 5.0)
        px_5s = mid_hist_px[idx_5s] if idx_5s < len(mid_hist_px) else mid_hist_px[-1]
        s["ret_5s"] = ((px_5s - px_e) / px_e) * 10000.0 if px_e > 0 else 0.0

    ret_1s_arr = np.array([s["ret_1s"] for s in sample_records])
    ret_5s_arr = np.array([s["ret_5s"] for s in sample_records])
    sig_depth_arr = np.array([s["s_depth"] for s in sample_records])

    # Directional return for active depth signals (|S| >= 0.25)
    active_idx = np.where(np.abs(sig_depth_arr) >= 0.25)[0]
    dir_rets_1s = np.sign(sig_depth_arr[active_idx]) * ret_1s_arr[active_idx]
    dir_rets_5s = np.sign(sig_depth_arr[active_idx]) * ret_5s_arr[active_idx]

    N = len(dir_rets_1s)
    mean_1s = float(np.mean(dir_rets_1s))
    mean_5s = float(np.mean(dir_rets_5s))

    # 3. Autocorrelation Function (ACF) up to lag 10
    def compute_acf(series, max_lag=10):
        s_dm = series - np.mean(series)
        var = np.var(series)
        if var < 1e-12:
            return [1.0] + [0.0] * max_lag
        acf = [1.0]
        for lag in range(1, max_lag + 1):
            cov = np.mean(s_dm[:-lag] * s_dm[lag:])
            acf.append(float(cov / var))
        return acf

    acf_1s = compute_acf(dir_rets_1s, 10)
    acf_5s = compute_acf(dir_rets_5s, 10)

    # 4. Newey-West HAC Standard Errors & Effective Sample Size
    def compute_newey_west(series, lag_L):
        N = len(series)
        dm = series - np.mean(series)
        gamma_0 = float(np.var(series))
        gamma_sum = 0.0
        for j in range(1, lag_L + 1):
            weight = 1.0 - (j / (lag_L + 1.0))
            cov = float(np.mean(dm[:-j] * dm[j:]))
            gamma_sum += 2.0 * weight * cov
        omega_hac = max(1e-12, gamma_0 + gamma_sum)
        se_hac = math.sqrt(omega_hac / N)
        se_ols = math.sqrt(gamma_0 / N)
        # Effective N
        n_eff = N * (gamma_0 / omega_hac)
        return se_ols, se_hac, n_eff

    # Lag truncation: L = ceil(tau / dt)
    se_ols_1s, se_hac_1s, n_eff_1s = compute_newey_west(
        dir_rets_1s, lag_L=2
    )  # 1s with 0.5s dt -> L=2
    se_ols_5s, se_hac_5s, n_eff_5s = compute_newey_west(
        dir_rets_5s, lag_L=10
    )  # 5s with 0.5s dt -> L=10

    t_ols_1s = mean_1s / se_ols_1s if se_ols_1s > 0 else 0.0
    t_hac_1s = mean_1s / se_hac_1s if se_hac_1s > 0 else 0.0
    t_ols_5s = mean_5s / se_ols_5s if se_ols_5s > 0 else 0.0
    t_hac_5s = mean_5s / se_hac_5s if se_hac_5s > 0 else 0.0

    # 5. Moving Block Bootstrap (B=1000, Block length = 20 samples = 10s)
    B = 1000
    block_len = 20
    num_blocks = N // block_len

    np.random.seed(42)
    boot_means_1s = []
    boot_means_5s = []

    for _ in range(B):
        # Sample block starting indices with replacement
        start_indices = np.random.randint(0, N - block_len + 1, size=num_blocks)
        boot_idx = np.concatenate(
            [np.arange(idx, idx + block_len) for idx in start_indices]
        )
        boot_means_1s.append(np.mean(dir_rets_1s[boot_idx]))
        boot_means_5s.append(np.mean(dir_rets_5s[boot_idx]))

    ci95_boot_1s = [
        float(np.percentile(boot_means_1s, 2.5)),
        float(np.percentile(boot_means_1s, 97.5)),
    ]
    ci95_boot_5s = [
        float(np.percentile(boot_means_5s, 2.5)),
        float(np.percentile(boot_means_5s, 97.5)),
    ]

    integrity_audit = {
        "sample_metadata": {
            "stream": "BINANCE:BTC/USD",
            "holdout_samples_total": len(sample_records),
            "holdout_active_depth_signals": N,
        },
        "autocorrelation": {
            "acf_1s_lags_0_to_10": [round(x, 4) for x in acf_1s],
            "acf_5s_lags_0_to_10": [round(x, 4) for x in acf_5s],
            "first_order_autocorr_1s": round(acf_1s[1], 4),
            "first_order_autocorr_5s": round(acf_5s[1], 4),
        },
        "effective_sample_size": {
            "nominal_N": N,
            "n_eff_1s": round(n_eff_1s, 1),
            "n_eff_ratio_1s": round(n_eff_1s / N, 3),
            "n_eff_5s": round(n_eff_5s, 1),
            "n_eff_ratio_5s": round(n_eff_5s / N, 3),
        },
        "hac_standard_errors": {
            "1s_horizon": {
                "mean_bps": round(mean_1s, 4),
                "se_ols": round(se_ols_1s, 5),
                "se_hac": round(se_hac_1s, 5),
                "hac_inflation_ratio": round(se_hac_1s / se_ols_1s, 3),
                "t_stat_ols": round(t_ols_1s, 2),
                "t_stat_hac": round(t_hac_1s, 2),
            },
            "5s_horizon": {
                "mean_bps": round(mean_5s, 4),
                "se_ols": round(se_ols_5s, 5),
                "se_hac": round(se_hac_5s, 5),
                "hac_inflation_ratio": round(se_hac_5s / se_ols_5s, 3),
                "t_stat_ols": round(t_ols_5s, 2),
                "t_stat_hac": round(t_hac_5s, 2),
            },
        },
        "block_bootstrap": {
            "iterations": B,
            "block_length_samples": block_len,
            "block_length_seconds": block_len * 0.5,
            "1s_horizon_mean_bps": round(mean_1s, 4),
            "1s_horizon_ci95_boot": [round(x, 4) for x in ci95_boot_1s],
            "5s_horizon_mean_bps": round(mean_5s, 4),
            "5s_horizon_ci95_boot": [round(x, 4) for x in ci95_boot_5s],
        },
        "multiple_testing": {
            "total_hypotheses": m,
            "bonferroni_critical_alpha": bonf_alpha,
            "significant_hypotheses_bonferroni": sum(
                1 for r in p_val_records if r["sig_bonferroni"]
            ),
            "significant_hypotheses_fdr_q05": sum(
                1 for r in p_val_records if r["sig_bh_fdr"]
            ),
            "rankings": p_val_records[:15],
        },
    }

    with open(output_json, "w") as f:
        json.dump(integrity_audit, f, indent=2)

    print("\n" + "=" * 80)
    print("ECONOMETRIC & STATISTICAL INTEGRITY SCORECARD")
    print("=" * 80)
    print(f"Nominal Sample Size N: {N:,}")
    print(
        f"1-Second Horizon:  SE_OLS = {se_ols_1s:.5f} | SE_HAC = {se_hac_1s:.5f} (x{se_hac_1s / se_ols_1s:.2f}) | t_HAC = {t_hac_1s:.2f} | N_eff = {n_eff_1s:,.0f}"
    )
    print(
        f"1-Second Block Bootstrap 95% CI: [{ci95_boot_1s[0]:+.4f}, {ci95_boot_1s[1]:+.4f}] bps"
    )
    print(
        f"5-Second Horizon:  SE_OLS = {se_ols_5s:.5f} | SE_HAC = {se_hac_5s:.5f} (x{se_hac_5s / se_ols_5s:.2f}) | t_HAC = {t_hac_5s:.2f} | N_eff = {n_eff_5s:,.0f}"
    )
    print(
        f"5-Second Block Bootstrap 95% CI: [{ci95_boot_5s[0]:+.4f}, {ci95_boot_5s[1]:+.4f}] bps"
    )
    print(
        f"Multiple Testing: {integrity_audit['multiple_testing']['significant_hypotheses_fdr_q05']} / {m} hypotheses survive Benjamini-Hochberg FDR (Q=0.05)."
    )
    print("=" * 80)
    print(f"Audit results successfully written to: {output_json}")


if __name__ == "__main__":
    run_statistical_integrity_verification()
