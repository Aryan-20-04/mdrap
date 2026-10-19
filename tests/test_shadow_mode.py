"""Tests for MDRAP Shadow-Mode PoC Evaluation Framework (Phase C)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import pytest

from mdrap.models import RawEvent, QualityStatus
from mdrap.shadow import ShadowModeEvaluator, ShadowEvaluationResult
from scripts.shadow_poc import generate_synthetic_poc_feed


def test_shadow_evaluator_confusion_matrix():
    """Verify precision, recall, and false positive calculation against known ground truth."""
    import time

    now = time.time()
    evaluator = ShadowModeEvaluator()

    # Create 4 test events:
    # 1. Clean event, baseline says clean -> TRUE_NEGATIVE
    # 2. Crossed quote, baseline says anomaly -> TRUE_POSITIVE
    # 3. Clean event, baseline says anomaly -> MISSED_ANOMALY
    # 4. Negative price, baseline says clean -> FALSE_POSITIVE
    events = [
        RawEvent(
            source="BINANCE",
            payload={
                "instrument": "BTC-USDT",
                "event_type": "QUOTE",
                "exchange_ts": now,
                "sequence": 1,
                "bid": 60000.0,
                "ask": 60002.0,
            },
            receive_timestamp=now + 0.001,
            raw_id="ev_tn",
        ),
        RawEvent(
            source="BINANCE",
            payload={
                "instrument": "BTC-USDT",
                "event_type": "QUOTE",
                "exchange_ts": now + 0.1,
                "sequence": 2,
                "bid": 60005.0,
                "ask": 60001.0,  # crossed -> INVALID / SUSPICIOUS
            },
            receive_timestamp=now + 0.101,
            raw_id="ev_tp",
        ),
        RawEvent(
            source="BINANCE",
            payload={
                "instrument": "BTC-USDT",
                "event_type": "QUOTE",
                "exchange_ts": now + 0.2,
                "sequence": 3,
                "bid": 60002.0,
                "ask": 60004.0,  # clean -> VALID
            },
            receive_timestamp=now + 0.201,
            raw_id="ev_fn",
        ),
        RawEvent(
            source="BINANCE",
            payload={
                "instrument": "BTC-USDT",
                "event_type": "QUOTE",
                "exchange_ts": now + 0.3,
                "sequence": 4,
                "bid": 60010.0,
                "ask": 60005.0,  # crossed -> INVALID / SUSPICIOUS
            },
            receive_timestamp=now + 0.301,
            raw_id="ev_fp",
        ),
    ]

    baseline_tags = {
        "ev_tn": {"is_anomaly": False, "reason": "Normal quote"},
        "ev_tp": {"is_anomaly": True, "reason": "Crossed market"},
        "ev_fn": {
            "is_anomaly": True,
            "reason": "Simulated external ground truth alert",
        },
        "ev_fp": {"is_anomaly": False, "reason": "Considered normal by baseline"},
    }

    res = evaluator.evaluate_stream(events, baseline_tags)

    assert res.total_events == 4
    assert res.true_positives == 1
    assert res.true_negatives == 1
    assert res.false_positives == 1
    assert res.missed_anomalies == 1

    # Precision = TP / (TP + FP) = 1 / 2 = 0.5
    assert res.precision == 0.5
    # Recall = TP / (TP + FN) = 1 / 2 = 0.5
    assert res.recall == 0.5
    # False Positive Rate = FP / (FP + TN) = 1 / 2 = 0.5
    assert res.false_positive_rate == 0.5


def test_shadow_evaluator_synthetic_feed():
    """Verify synthetic feed generation and stream evaluation execution."""
    events, baseline = generate_synthetic_poc_feed(count=500)
    assert len(events) == 500
    assert len(baseline) > 0

    evaluator = ShadowModeEvaluator()
    result = evaluator.evaluate_stream(events, baseline)

    assert result.total_events == 500
    assert result.elapsed_seconds > 0
    assert result.throughput_eps > 0
    assert result.true_positives > 0

    report = result.generate_report()
    assert "# MDRAP Shadow-Mode PoC Evaluation Report" in report
    assert "Executive Summary" in report
    assert "Sample Discrepancies" in report


def test_shadow_evaluator_to_dict_and_json():
    """Verify serialization to dictionary and valid JSON."""
    events, baseline = generate_synthetic_poc_feed(count=50)
    evaluator = ShadowModeEvaluator()
    result = evaluator.evaluate_stream(events, baseline)

    d = result.to_dict()
    assert "total_events" in d
    assert "metrics" in d
    assert "confusion_matrix" in d

    s = json.dumps(d)
    parsed = json.loads(s)
    assert parsed["total_events"] == 50


def test_shadow_cli_execution():
    """Verify scripts/shadow_poc.py CLI runs and emits output files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out_md = os.path.join(tmpdir, "report.md")
        out_json = os.path.join(tmpdir, "metrics.json")

        cmd = [
            sys.executable,
            "scripts/shadow_poc.py",
            "--limit",
            "100",
            "--output",
            out_md,
            "--json-output",
            out_json,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        assert proc.returncode == 0, f"CLI error: {proc.stderr}"
        assert os.path.exists(out_md)
        assert os.path.exists(out_json)

        with open(out_json, "r", encoding="utf-8") as f:
            data = json.load(f)
            assert data["total_events"] == 100
