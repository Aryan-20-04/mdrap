"""
Unit and integration tests for MDRAP Multi-Directional Stress Testing & Scale Analysis Suite.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

pytestmark = pytest.mark.slow  # ponytail: stress tests skip by default

from cli import build_parser
import stresstest


def test_get_rss_mb():
    rss = stresstest.get_rss_mb()
    assert isinstance(rss, float)
    assert rss >= 0.0


def test_compute_latencies_us():
    sample_ns = [1000, 2000, 3000, 4000, 5000, 10000]
    lat = stresstest.compute_latencies_us(sample_ns)
    assert lat["p50"] > 0.0
    assert lat["max"] == 10.0
    assert lat["mean"] > 0.0


def test_stress_gateway():
    res = stresstest.stress_gateway(num_events=1000)
    assert res["module"] == "Gateway / Normalizer"
    assert res["events"] == 1000
    assert res["throughput_eps"] > 0.0
    assert "latencies_us" in res


def test_stress_quality_engine():
    res = stresstest.stress_quality_engine(num_events=1000)
    assert res["module"] == "7-Rule Quality Engine"
    assert res["python_eps"] > 0.0
    if res["has_c_fastpath"]:
        assert res["c_fastpath_eps"] > 0.0


def test_stress_bbo_engine():
    res = stresstest.stress_bbo_engine(num_events=1000, num_instruments=5)
    assert res["module"] == "Consolidated BBO Engine"
    assert res["events"] == 1000
    assert res["throughput_eps"] > 0.0


def test_stress_storage_disk_io():
    res_list = stresstest.stress_storage_disk_io(num_events=1000, batch_sizes=[500])
    assert len(res_list) == 1
    assert res_list[0]["batch_size"] == 500
    assert res_list[0]["throughput_eps"] > 0.0


def test_stress_ipc_socket():
    res = stresstest.stress_ipc_socket(num_events=1000, num_clients=2)
    assert res["module"] == "IPC Streaming TCP Socket"
    assert res["ticks_broadcast"] == 1000
    assert res["throughput_eps"] > 0.0


def test_stress_end_to_end():
    res_list = stresstest.stress_end_to_end(levels=[1000])
    assert len(res_list) == 1
    r = res_list[0]
    assert r["level"] == 1000
    assert r["throughput_eps"] > 0.0
    assert r["valid_count"] + r["invalid_count"] + r["suspicious_count"] == 1000


def test_analyze_scale_boundaries():
    mod_results = {
        "quality": {"python_eps": 50000.0, "c_fastpath_eps": 10000000.0},
        "storage": [{"throughput_eps": 40000.0}],
    }
    e2e = [{"throughput_eps": 25000.0}]
    analysis = stresstest.analyze_scale_boundaries(mod_results, e2e)
    assert "scale_1m" in analysis
    assert "scale_1b" in analysis
    assert analysis["scale_1m"]["headroom_multiplier"] > 50.0
    assert len(analysis["scale_1b"]["bottlenecks"]) > 0


def test_cli_stress_dispatch(capsys):
    parser = build_parser()
    args = parser.parse_args(["stress", "--module", "gateway", "-e", "500"])
    args.func(args)
    captured = capsys.readouterr()
    assert "Direction A: Individual Module Isolation Stress Benchmarks" in captured.out
    assert "Gateway Normalizer" in captured.out
