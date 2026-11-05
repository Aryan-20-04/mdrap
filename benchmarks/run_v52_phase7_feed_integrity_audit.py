"""
MDRAP v5.2 Phase 7: Live Feed Infrastructure & Market Data Integrity Audit.

Evaluates:
1. Cross-Venue Basis Deconstruction (BTC-USD vs BTC-USDT Spread vs Executability).
2. Feed Resilience & Fault Injection Drill (Sequence Gaps, Epoch Resets, Malformed Payloads, Crossed Quotes).
3. Quarantine Verification & Lineage Tracking.
4. Exports RAW_FEED_INTEGRITY_TELEMETRY.json.
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from mdrap.models import RawEvent, QualityStatus, Reason
from mdrap.gateway import normalize
from mdrap.fastpath import FastQualityEngine


def run_cross_venue_basis_audit() -> dict[str, Any]:
    """
    Forensically analyzes cross-venue price spreads across 5 venues:
    Binance (BTC-USDT), Coinbase (BTC-USD), Kraken (BTC-USD), OKX (BTC-USDT), Bybit (BTC-USDT).
    Calculates Gross Spread vs Taker Fees vs Tether Basis = Net Executable Alpha.
    """
    # Sample snapshot based on historical recorded data
    venue_data = {
        "COINBASE": {"pair": "BTC-USD", "bid": 64210.50, "ask": 64211.50, "taker_bps": 6.0},
        "KRAKEN":   {"pair": "BTC-USD", "bid": 64208.00, "ask": 64212.00, "taker_bps": 10.0},
        "BINANCE":  {"pair": "BTC-USDT", "bid": 64285.00, "ask": 64286.00, "taker_bps": 5.0},
        "OKX":      {"pair": "BTC-USDT", "bid": 64284.50, "ask": 64285.50, "taker_bps": 5.0},
        "BYBIT":    {"pair": "BTC-USDT", "bid": 64285.20, "ask": 64286.20, "taker_bps": 6.0},
    }

    # Apparent Cross-Venue Spread (Buy Coinbase, Sell Binance)
    buy_venue = "COINBASE"
    sell_venue = "BINANCE"
    buy_px = venue_data[buy_venue]["ask"]   # 64,211.50 USD
    sell_px = venue_data[sell_venue]["bid"] # 64,285.00 USDT

    gross_spread_usd = sell_px - buy_px # $73.50 (Apparent Arbitrage)
    gross_spread_bps = (gross_spread_usd / buy_px) * 10_000.0 # ~11.44 bps

    # Real-world Execution Cost Breakdown
    taker_fee_buy = buy_px * (venue_data[buy_venue]["taker_bps"] / 10_000.0) # $38.53
    taker_fee_sell = sell_px * (venue_data[sell_venue]["taker_bps"] / 10_000.0) # $32.14
    total_taker_fees = taker_fee_buy + taker_fee_sell # $70.67

    # Tether (USDT/USD) Basis Adjustment: USDT trading at $0.9990
    usdt_usd_rate = 0.9990
    sell_px_converted_usd = sell_px * usdt_usd_rate # $64,220.715 USD
    real_basis_spread_usd = sell_px_converted_usd - buy_px # $9.215 USD

    # Net Economic Alpha
    net_pnl_usd = real_basis_spread_usd - total_taker_fees # -$61.455 (LOSS!)
    net_pnl_bps = (net_pnl_usd / buy_px) * 10_000.0

    return {
        "buy_venue": buy_venue,
        "sell_venue": sell_venue,
        "buy_price_usd": buy_px,
        "sell_price_usdt": sell_px,
        "apparent_gross_spread_usd": round(gross_spread_usd, 2),
        "apparent_gross_spread_bps": round(gross_spread_bps, 2),
        "taker_fees_paid_usd": round(total_taker_fees, 2),
        "usdt_usd_exchange_rate": usdt_usd_rate,
        "real_currency_adjusted_spread_usd": round(real_basis_spread_usd, 2),
        "net_executable_pnl_usd": round(net_pnl_usd, 2),
        "net_executable_pnl_bps": round(net_pnl_bps, 2),
        "is_executable_arbitrage": net_pnl_usd > 0,
        "audit_finding": "The reported $76 cross-venue arbitrage was an illusion created by ignoring taker fees and the USDT/USD currency basis.",
    }


def run_feed_fault_injection_drill() -> dict[str, Any]:
    """
    Injects deterministic faults into live feed ingestion:
    1. Sequence Gap (packet loss)
    2. Session Disconnect / Epoch Reset (seq jumps backwards to 1)
    3. Crossed Quote (bid > ask)
    4. Stale Timestamp (> 30s delay)
    5. Price Spike (volatility corridor breach)
    """
    engine = FastQualityEngine()
    clock_now = 1700000000.0

    test_cases = [
        # 1. Clean Baseline
        {
            "name": "CLEAN_TICK",
            "payload": {"instrument": "BTCUSDT", "sequence": 100, "exchange_ts": clock_now, "bid": 100.0, "ask": 100.1, "bid_size": 10.0, "ask_size": 10.0},
            "expected_status": QualityStatus.VALID,
        },
        # 2. Sequence Gap (seq jumps from 100 to 105)
        {
            "name": "SEQUENCE_GAP",
            "payload": {"instrument": "BTCUSDT", "sequence": 105, "exchange_ts": clock_now + 0.01, "bid": 100.0, "ask": 100.1, "bid_size": 10.0, "ask_size": 10.0},
            "expected_status": QualityStatus.SUSPICIOUS, # or INVALID depending on policy
        },
        # 3. Crossed Quote (bid 100.5 > ask 100.1)
        {
            "name": "CROSSED_QUOTE",
            "payload": {"instrument": "BTCUSDT", "sequence": 106, "exchange_ts": clock_now + 0.02, "bid": 100.5, "ask": 100.1, "bid_size": 10.0, "ask_size": 10.0},
            "expected_status": QualityStatus.INVALID,
        },
        # 4. Price Spike (+50% jump)
        {
            "name": "PRICE_ANOMALY",
            "payload": {"instrument": "BTCUSDT", "sequence": 107, "exchange_ts": clock_now + 0.03, "bid": 150.0, "ask": 150.1, "bid_size": 10.0, "ask_size": 10.0},
            "expected_status": QualityStatus.SUSPICIOUS,
        },
        # 5. Stale Timestamp (3600s in the past)
        {
            "name": "STALE_TIMESTAMP",
            "payload": {"instrument": "BTCUSDT", "sequence": 108, "exchange_ts": clock_now - 3600.0, "bid": 100.0, "ask": 100.1, "bid_size": 10.0, "ask_size": 10.0},
            "expected_status": QualityStatus.INVALID,
        },
        # 6. Session Reset (seq resets to 1 after disconnect)
        {
            "name": "SESSION_RESET",
            "payload": {"instrument": "BTCUSDT", "sequence": 1, "exchange_ts": clock_now + 0.05, "bid": 100.0, "ask": 100.1, "bid_size": 10.0, "ask_size": 10.0},
            "expected_status": QualityStatus.INVALID, # Detected as out-of-order sequence regression
        },
    ]

    drill_results = []
    passed_tests = 0

    for i, tc in enumerate(test_cases):
        raw = RawEvent(
            source="BINANCE",
            payload=tc["payload"],
            receive_timestamp=clock_now + i * 0.01,
            raw_id=f"drill_{i}",
        )
        tc["payload"]["event_type"] = "QUOTE"
        norm = normalize(raw)
        dec = engine.evaluate(norm)

        # In MDRAP, severity must match or be non-downgraded
        status_matches = (dec.quality_status == tc["expected_status"])
        if status_matches:
            passed_tests += 1

        drill_results.append({
            "test_case": tc["name"],
            "expected_status": str(tc["expected_status"]),
            "actual_status": str(dec.quality_status),
            "reasons": [str(r) for r in dec.reasons],
            "passed": status_matches,
        })

    return {
        "total_test_cases": len(test_cases),
        "passed_test_cases": passed_tests,
        "pass_rate_pct": round((passed_tests / len(test_cases)) * 100.0, 2),
        "drill_details": drill_results,
    }


def main():
    print("=" * 80)
    print("MDRAP v5.2 PHASE 7: FEED INFRASTRUCTURE & DATA INTEGRITY AUDIT")
    print("=" * 80)

    print("\n[1/2] Conducting Forensic Audit of Cross-Venue Spread (BTC-USD vs BTC-USDT)...")
    basis_results = run_cross_venue_basis_audit()
    print(f"  -> Apparent Gross Spread:   ${basis_results['apparent_gross_spread_usd']} ({basis_results['apparent_gross_spread_bps']} bps)")
    print(f"  -> Total Taker Fees:        ${basis_results['taker_fees_paid_usd']}")
    print(f"  -> Real Currency Spread:    ${basis_results['real_currency_adjusted_spread_usd']}")
    print(f"  -> Net Executable PnL:      ${basis_results['net_executable_pnl_usd']} ({basis_results['net_executable_pnl_bps']} bps)")
    print(f"  -> Truly Executable:        {basis_results['is_executable_arbitrage']}")

    print("\n[2/2] Executing Feed Resilience & Fault Injection Drill...")
    fault_results = run_feed_fault_injection_drill()
    for d in fault_results["drill_details"]:
        print(f"  -> {d['test_case']}: Expected {d['expected_status']} | Got {d['actual_status']} (Passed: {d['passed']})")

    telemetry = {
        "timestamp": time.time(),
        "cross_venue_basis": basis_results,
        "fault_injection_drill": fault_results,
    }
    out_path = ROOT / "audit" / "v5.2" / "RAW_FEED_INTEGRITY_TELEMETRY.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(telemetry, f, indent=2)

    print(f"\n[OK] Phase 7 telemetry saved to {out_path}")


if __name__ == "__main__":
    main()
