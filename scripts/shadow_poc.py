#!/usr/bin/env python3
"""
MDRAP Shadow-Mode PoC CLI Evaluation Tool.

Executes a non-intrusive evaluation of recorded market data streams against
baseline tags, reporting precision, recall, false-positive rate, and throughput.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

# Ensure repo root and src/ are importable
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
_SRC_DIR = os.path.join(_REPO_ROOT, "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from mdrap.models import RawEvent
from mdrap.shadow import ShadowModeEvaluator, ShadowEvaluationResult


def generate_synthetic_poc_feed(count: int = 10000) -> tuple[list[RawEvent], dict[str, dict]]:
    """Generate a realistic test stream with injected market anomalies for PoC demonstration."""
    import random

    rng = random.Random(42)
    events: list[RawEvent] = []
    baseline: dict[str, dict] = {}
    base_ts = time.time() - 3600.0

    symbols = ["BTC-USDT", "ETH-USDT", "SOL-USDT"]
    sources = ["BINANCE", "KRAKEN", "COINBASE"]

    for i in range(count):
        raw_id = f"raw_{i:07d}"
        inst = rng.choice(symbols)
        src = rng.choice(sources)
        event_ts = base_ts + i * 0.001

        # Inject periodic anomalies (~3% anomaly rate)
        anomaly_type = None
        if i % 150 == 0:
            anomaly_type = "CROSSED_QUOTE"
        elif i % 250 == 0:
            anomaly_type = "PRICE_NEGATIVE"
        elif i % 400 == 0:
            anomaly_type = "TIMESTAMP_REGRESSION"

        if anomaly_type == "CROSSED_QUOTE":
            bid = 65000.0 + rng.uniform(50.0, 100.0)
            ask = bid - rng.uniform(2.0, 10.0)  # crossed
            p = {
                "instrument": inst,
                "event_type": "QUOTE",
                "exchange_ts": event_ts,
                "sequence": i + 1,
                "bid": bid,
                "ask": ask,
                "bid_size": 1.0,
                "ask_size": 1.0,
            }
            baseline[raw_id] = {"is_anomaly": True, "reason": "Crossed market detected"}
        elif anomaly_type == "PRICE_NEGATIVE":
            p = {
                "instrument": inst,
                "event_type": "TRADE",
                "exchange_ts": event_ts,
                "sequence": i + 1,
                "price": -10.0,
                "quantity": 0.5,
            }
            baseline[raw_id] = {"is_anomaly": True, "reason": "Negative trade price"}
        elif anomaly_type == "TIMESTAMP_REGRESSION":
            p = {
                "instrument": inst,
                "event_type": "TRADE",
                "exchange_ts": event_ts - 500.0,  # 500s in past (stale)
                "sequence": i + 1,
                "price": 65000.0,
                "quantity": 0.5,
            }
            baseline[raw_id] = {"is_anomaly": True, "reason": "Stale historical timestamp"}
        else:
            p = {
                "instrument": inst,
                "event_type": "TRADE" if rng.random() > 0.5 else "QUOTE",
                "exchange_ts": event_ts,
                "sequence": i + 1,
                "price": 65000.0 + rng.uniform(-10.0, 10.0),
                "quantity": rng.uniform(0.1, 2.0),
                "bid": 64999.0,
                "ask": 65001.0,
            }

        raw = RawEvent(
            source=src,
            payload=p,
            receive_timestamp=event_ts + 0.0005,
            raw_id=raw_id,
        )
        events.append(raw)

    return events, baseline


def parse_jsonl_stream(filepath: str, limit: int | None = None) -> list[RawEvent]:
    events = []
    with open(filepath, "r", encoding="utf-8") as f:
        for idx, line in enumerate(f):
            if limit and idx >= limit:
                break
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            raw = RawEvent(
                source=data.get("source", "UNKNOWN"),
                payload=data.get("payload", {}),
                receive_timestamp=float(data.get("receive_ts", time.time())),
                raw_id=data.get("raw_id", f"raw_{idx:07d}"),
            )
            events.append(raw)
    return events


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run MDRAP Shadow-Mode PoC Evaluation"
    )
    parser.add_argument(
        "--input",
        type=str,
        default=None,
        help="Path to JSONL file containing recorded raw events (if omitted, synthetic demo stream is generated)",
    )
    parser.add_argument(
        "--baseline",
        type=str,
        default=None,
        help="Optional path to JSON file containing baseline anomaly annotations",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Path to save markdown evaluation report (e.g. audit/shadow_poc_report.md)",
    )
    parser.add_argument(
        "--json-output",
        type=str,
        default=None,
        help="Path to save JSON metrics output (e.g. audit/shadow_poc_metrics.json)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of events to process",
    )

    args = parser.parse_args()

    baseline_tags: dict[str, dict] = {}
    if args.baseline and os.path.exists(args.baseline):
        with open(args.baseline, "r", encoding="utf-8") as f:
            baseline_tags = json.load(f)

    if args.input:
        print(f"[*] Loading raw event stream from: {args.input}")
        events = parse_jsonl_stream(args.input, limit=args.limit)
    else:
        print("[*] No input file specified. Generating 10,000-event synthetic demo stream...")
        events, baseline_tags = generate_synthetic_poc_feed(count=args.limit or 10000)

    print(f"[*] Starting shadow evaluation on {len(events):,} events...")
    evaluator = ShadowModeEvaluator()
    result: ShadowEvaluationResult = evaluator.evaluate_stream(events, baseline_tags)

    report_md = result.generate_report()
    metrics_json = json.dumps(result.to_dict(), indent=2)

    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report_md)
        print(f"[+] Markdown report saved to: {args.output}")

    if args.json_output:
        os.makedirs(os.path.dirname(os.path.abspath(args.json_output)), exist_ok=True)
        with open(args.json_output, "w", encoding="utf-8") as f:
            f.write(metrics_json)
        print(f"[+] JSON metrics saved to: {args.json_output}")

    print("\n" + "=" * 72)
    print("MDRAP SHADOW-MODE EVALUATION SUMMARY".center(72))
    print("=" * 72)
    print(f"Total Events Evaluated: {result.total_events:,}")
    print(f"Throughput:             {result.throughput_eps:,.1f} events/sec")
    print(f"Precision:              {result.precision * 100:.1f}%")
    print(f"Recall:                 {result.recall * 100:.1f}%")
    print(f"False Positive Rate:    {result.false_positive_rate * 100:.2f}%")
    print(f"True Positives:         {result.true_positives:,}")
    print(f"False Positives:        {result.false_positives:,}")
    print(f"Missed Anomalies:       {result.missed_anomalies:,}")
    print("=" * 72)


if __name__ == "__main__":
    main()
