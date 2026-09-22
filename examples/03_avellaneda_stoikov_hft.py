"""
MDRAP Institutional High-Frequency Trading (HFT) Strategy: Avellaneda-Stoikov Market Maker.

This script demonstrates an industry-grade quantitative market-making strategy (Avellaneda & Stoikov, 2008)
running on MDRAP's event-driven Strategy SDK with Native C Fastpath Quality validation.

Model Overview:
1. Reference Mid-Price & Micro-Price:
   S_mid = (P_ask + P_bid) / 2
   P_micro = S_mid + 0.5 * spread * Imbalance (OBI)

2. Inventory-Aware Reservation (Indifference) Price:
   r(s, q) = P_micro - q * gamma * sigma^2
   - q > 0 (Long inventory)  -> r drops -> quotes skew lower to shed inventory
   - q < 0 (Short inventory) -> r rises -> quotes skew higher to cover shorts

3. Optimal Spread Calculation:
   delta = 0.5 * [gamma * sigma^2 + (2 / gamma) * ln(1 + gamma / kappa)]
   - gamma: Risk aversion parameter
   - kappa: Order book liquidity intensity parameter

4. Native C Fastpath Data Quality Integration:
   - Evaluates incoming ticks with the Native C hot-path engine (< 50 ns).
   - Quarantines and rejects INVALID ticks (crossed quotes, negative prices, schema errors).
   - Detects SUSPICIOUS ticks (sudden 6-sigma price jumps, stale quotes) and activates
     a 3x spread expansion defense to prevent toxic adverse selection.
"""
from __future__ import annotations

import os
import sys
import time

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Ensure src/ is on path
_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from models import CanonicalEvent, EventType, QualityStatus
from gateway import ingest, normalize
from simulator import FeedSimulator, SimulatorConfig
from strategy_sdk import AvellanedaStoikovStrategy, RiskLimits
from fastpath import FastQualityEngine, is_available


def run_avellaneda_stoikov_simulation(
    symbol: str = "AAPL",
    events_count: int = 2000,
    seed: int = 42,
    gamma: float = 0.1,
    kappa: float = 1.5,
    quote_size: float = 50.0,
    max_inventory: float = 500.0,
):
    print("=" * 76)
    print("  MDRAP Institutional HFT Engine — Avellaneda-Stoikov Market Maker (AS-MM)")
    print("=" * 76)
    print(f"[*] Universe:               {symbol}")
    print(f"[*] Total Events:           {events_count:,} (deterministic seed={seed})")
    print(f"[*] Risk Aversion (gamma):     {gamma}")
    print(f"[*] Liquidity Intensity (kappa):{kappa}")
    print(f"[*] Quote Lot Size:         {quote_size:.0f} shares")
    print(f"[*] Max Inventory Limit:    +/-{max_inventory:.0f} shares")

    # 1. Initialize Strategy with Pre-Trade Risk Limits
    risk_limits = RiskLimits(
        max_order_size=quote_size * 2,
        max_position_size=max_inventory,
        price_collar_bps=50.0,
        max_drawdown_pct=5.0,
    )
    strategy = AvellanedaStoikovStrategy(
        symbol=symbol,
        gamma=gamma,
        kappa=kappa,
        quote_size=quote_size,
        max_inventory=max_inventory,
        min_spread_bps=1.0,
        vol_window=50,
        risk_limits=risk_limits,
    )

    # 2. Initialize Native C Fastpath Quality Engine
    has_native = is_available()
    print(f"[*] Data Quality Shield:    {'Native C Accelerator (_fastpath_native.dll)' if has_native else 'Pure Python Engine'}")
    quality_engine = FastQualityEngine()

    # 3. Simulate Deterministic Market Feed
    sim_cfg = SimulatorConfig(
        seed=seed,
        num_events=events_count,
        instruments=[symbol],
        crossed_quote_rate=0.005,  # 0.5% crossed quotes to test defense
        price_anomaly_rate=0.005,  # 0.5% price spikes to test toxic flow guard
    )
    simulator = FeedSimulator(sim_cfg)

    quarantined_count = 0
    suspicious_count = 0
    valid_count = 0

    print("\n[+] Starting event streaming loop...")
    t_start = time.perf_counter()
    strategy.on_start()

    for raw, _ in simulator.generate():
        try:
            raw_ing = ingest(raw)
            canonical = normalize(raw_ing)
        except Exception:
            continue

        if canonical.instrument_id != symbol:
            continue

        # Fastpath Data Quality Evaluation (< 50ns)
        canonical = quality_engine.evaluate(canonical)

        # Protection 1: Quarantine & drop INVALID quotes (e.g. crossed books)
        if canonical.quality_status == QualityStatus.INVALID:
            quarantined_count += 1
            continue

        # Protection 2: Tag SUSPICIOUS quotes (e.g. 6-sigma price jumps)
        if canonical.quality_status == QualityStatus.SUSPICIOUS:
            suspicious_count += 1
        else:
            valid_count += 1

        # Dispatch canonical event to strategy
        if canonical.event_type == EventType.QUOTE:
            strategy.on_quote(canonical)
        elif canonical.event_type == EventType.TRADE:
            strategy.on_tick(canonical)

    strategy.on_stop()
    elapsed_s = time.perf_counter() - t_start

    # 4. Compute Performance Summary & Metrics
    summary = strategy.performance_summary()
    fills = strategy.executor.fills

    print("\n" + "=" * 76)
    print(f"  STRATEGY EXECUTION TEAR-SHEET: {strategy.name.upper()}")
    print("=" * 76)
    print(f"  Throughput Speed:         {events_count / elapsed_s:,.0f} events/sec ({elapsed_s * 1000:.2f} ms total)")
    print(f"  Valid Ticks Processed:    {valid_count:,}")
    print(f"  Crossed Quotes Shielded:  {quarantined_count:,} (quarantined, zero bad fills)")
    print(f"  Toxic Spikes Defended:    {suspicious_count:,} (spread widened by 3x)")
    print("-" * 76)
    print(f"  Executed Trades (Fills):  {summary['total_trades']:,}")
    print(f"  Win Rate (Closed Trades): {summary['win_rate_pct']:.1f}%")
    print(f"  Realized Net P&L:         ${summary['realized_pnl']:+,.2f}")
    print(f"  Unrealized (MTM) P&L:     ${summary['unrealized_pnl']:+,.2f}")
    print(f"  Total Portfolio P&L:      ${summary['total_pnl']:+,.2f}")
    print(f"  Ending Account Equity:    ${summary['final_equity']:,.2f}")
    print(f"  Return on Capital:        {summary['return_pct']:+.2f}%")
    print(f"  Mean Execution Slippage:  {summary['avg_slippage_bps']:.2f} bps")
    print(f"  Ending Open Position:     {summary['positions'].get(symbol, 0.0):.0f} shares")
    print("=" * 76)

    # 5. Export Execution Audit Trail
    os.makedirs("data/reports", exist_ok=True)
    export_path = strategy.export_executions("data/reports/avellaneda_stoikov_fills.csv", format="csv")
    print(f"[+] Detailed execution ledger exported to: {export_path}")

    return summary


if __name__ == "__main__":
    run_avellaneda_stoikov_simulation(
        symbol="AAPL",
        events_count=2000,
        seed=42,
        gamma=0.1,
        kappa=1.5,
        quote_size=50.0,
        max_inventory=500.0,
    )
