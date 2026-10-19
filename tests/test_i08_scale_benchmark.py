"""Unit tests for Program I-8: Scaled Benchmark Harness."""

from __future__ import annotations

import sys
from pathlib import Path
import pytest

from benchmarks.bench_scale import run_scale_benchmark


def test_scale_benchmark_structure() -> None:
    """Test scaled benchmark produces valid telemetry schema across multiple universes."""
    scales = [10, 50]
    report = run_scale_benchmark(scales=scales, events_per_scale=100)

    assert "suite" in report
    assert "results" in report
    assert len(report["results"]) == 2

    for s in scales:
        s_key = str(s)
        assert s_key in report["results"]
        res = report["results"][s_key]
        assert res["universe_size"] == s
        assert res["events_measured"] == 100
        assert res["throughput_eps"] > 0
        lat = res["latency_us"]
        assert "p50" in lat
        assert "p95" in lat
        assert "p99" in lat
        assert "max" in lat
        assert lat["p50"] <= lat["p95"] <= lat["p99"] <= lat["max"]
