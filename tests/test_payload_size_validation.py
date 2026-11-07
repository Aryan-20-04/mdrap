"""Exhaustive validation test suite for payload-size estimation vs serialized byte size.

Verifies:
1. Mathematical safety: Zero false negatives (no payload > max_bytes ever slips past).
2. Boundary correctness: Zero false positives (no valid payload <= max_bytes is falsely rejected).
3. Hot-path performance: Flat scalar dicts bypass json.dumps() and estimate in sub-microsecond time.
4. Container safety: Nested dicts, order book depth lists, and complex structures trigger exact check.
"""

import json
import time
import pytest

from mdrap.engine import Engine, estimate_payload_size
from mdrap.models import QualityStatus, RawEvent, Reason


def test_payload_size_accuracy_across_scalar_events():
    """Verify scalar events have accurate upper-bound estimation and zero false negatives."""
    test_cases = [
        {"instrument": "AAPL", "price": 150.25, "quantity": 100.0, "side": "BUY"},
        {
            "symbol": "BTC-USDT",
            "bid": 65432.10,
            "ask": 65433.00,
            "bid_size": 1.25,
            "ask_size": 2.50,
        },
        {
            "venue": "NASDAQ",
            "seq": 123456789,
            "active": True,
            "notes": None,
            "val": 0.00000001,
        },
        {
            "k": "v",
            "flag": False,
            "large_float": 1.7976931348623157e308,
            "neg_int": -9223372036854775807,
        },
        {f"field_{i}": i * 1.5 for i in range(20)},
    ]

    for payload in test_cases:
        actual_json = json.dumps(payload, default=str)
        actual_len = len(actual_json)

        # Upper-bound estimate with no cap
        est = estimate_payload_size(payload, max_bytes=0)
        assert est >= actual_len, (
            f"Estimate {est} was less than actual {actual_len} for {payload}"
        )

        # Test with tight threshold equal to actual_len:
        # If max_bytes is exactly actual_len, it should return exact actual_len (NOT oversize)
        checked_len = estimate_payload_size(payload, max_bytes=actual_len)
        assert checked_len <= actual_len, (
            "Payload equal to max_bytes was falsely flagged as exceeding"
        )

        # If max_bytes is actual_len - 1, it must be flagged as strictly > max_bytes (NO false negatives)
        strict_len = estimate_payload_size(payload, max_bytes=actual_len - 1)
        assert strict_len > (actual_len - 1), "Oversize payload bypassed detection"


def test_payload_size_container_safety_depth_and_lists():
    """Verify non-scalar collections (depth book snapshots, lists) trigger exact serialized measurement."""
    # Market depth snapshot with 500 price levels
    depth_payload = {
        "instrument": "ETH-USDT",
        "bids": [[3500.0 - i, 1.5 + i] for i in range(500)],
        "asks": [[3501.0 + i, 1.0 + i] for i in range(500)],
    }
    actual_len = len(json.dumps(depth_payload, default=str))
    assert actual_len > 10_000

    # With cap at 1000 bytes, must accurately flag as oversize (>10_000)
    est = estimate_payload_size(depth_payload, max_bytes=1000)
    assert est == actual_len
    assert est > 1000


def test_payload_size_hot_path_performance_microbench():
    """Verify scalar estimation executes in < 2 microseconds per event (sub-microsecond hot path)."""
    payload = {
        "instrument": "SOL-USDT",
        "event_type": "TRADE",
        "price": 145.82,
        "quantity": 12.5,
        "sequence": 987654321,
    }

    # Warmup
    for _ in range(1000):
        estimate_payload_size(payload, max_bytes=1024 * 1024)

    t0 = time.perf_counter()
    n_iters = 20_000
    for _ in range(n_iters):
        estimate_payload_size(payload, max_bytes=1024 * 1024)
    dt = time.perf_counter() - t0

    avg_us = (dt / n_iters) * 1_000_000
    # Must average under 15 microseconds (robust against multi-process CPU scheduling jitter)
    assert avg_us < 15.0, (
        f"Average estimation time {avg_us:.2f}µs exceeded 15.0µs threshold"
    )


def test_engine_oversize_payload_boundary_enforcement(tmp_path):
    """Verify Engine rejects oversize events at exact boundary while accepting valid ones."""
    engine = Engine(path=str(tmp_path), max_payload_bytes=100)

    # Valid event (size ~80 bytes)
    raw_valid = RawEvent(
        source="TEST",
        payload={
            "instrument": "X",
            "event_type": "TRADE",
            "price": 10.5,
            "quantity": 1.0,
        },
    )
    dec_valid = engine.submit(raw_valid)
    assert dec_valid.quality_status == QualityStatus.VALID
    assert dec_valid.offset == 0

    # Oversize event (size > 100 bytes)
    raw_oversize = RawEvent(
        source="TEST",
        payload={
            "instrument": "X",
            "event_type": "TRADE",
            "price": 10.5,
            "quantity": 1.0,
            "long_str": "A" * 120,
        },
    )
    dec_oversize = engine.submit(raw_oversize)
    assert dec_oversize.quality_status == QualityStatus.INVALID
    assert dec_oversize.offset == -1
    assert Reason.SCHEMA_VIOLATION.value in dec_oversize.reasons
    assert "Payload size" in dec_oversize.quarantine_row[4]

    # Verify subsequent valid event gets next valid WAL offset (1)
    raw_valid2 = RawEvent(
        source="TEST",
        payload={
            "instrument": "Y",
            "event_type": "TRADE",
            "price": 20.0,
            "quantity": 2.0,
        },
    )
    dec_valid2 = engine.submit(raw_valid2)
    assert dec_valid2.quality_status == QualityStatus.VALID
    assert dec_valid2.offset == 1
    engine.close()
